"""Work against a live database: pull, plan, deploy.

    ords2apx pull -d rest/            export the schema's ORDS definitions into a project
    ords2apx plan rest/               show what deploy would change
    ords2apx deploy rest/ [--prune]   apply the changes

The connection comes from --dsn or $ORDS2APX_DSN, as user/password@host:port/service
(the password is prompted for when left out). Needs `pip install ords2apx[db]`.

The database side is read with ORDS_EXPORT.EXPORT_SCHEMA and converted like
any export, so plan compares like with like: each top-level component's
canonical .ords.apx text, locally and in the database.
"""

from __future__ import annotations

import difflib
import getpass
import os
from dataclasses import dataclass, field

from . import apx
from .apx import Component, Document
from .model import OrdsCall, calls_to_block, document_to_calls, sql_to_document
from .project import KIND_ORDER, merge
from .schema import Schema, default_schema

ENV_DSN = "ORDS2APX_DSN"

# call that removes a top-level component, by kind: (procedure, id parameter)
DELETE_CALLS = {
    "restModule": ("ORDS.DELETE_MODULE", "p_module_name"),
    "restRole": ("ORDS.DELETE_ROLE", "p_role_name"),
    "restPrivilege": ("ORDS.DELETE_PRIVILEGE", "p_name"),
    "restOauthClient": ("ORDS_SECURITY.DELETE_CLIENT", "p_name"),
    "restJwtProfile": ("ORDS_SECURITY.DELETE_JWT_PROFILE", None),
}
# kinds whose definition call fails when the component exists, so a change deletes first
RECREATE = {"restOauthClient", "restJwtProfile"}
# roles and privileges ORDS creates and maintains itself (AutoREST, SODA)
SYSTEM_PREFIXES = ("oracle.dbtools.", "oracle.soda.")
# statements an export adds that are not definitions
EXPORT_ONLY = ("ORDS.FINALIZE_IMPORT",)
# arrays whose order does not matter to ORDS
UNORDERED = {"roles", "modules", "patterns", "privileges"}


class DatabaseError(RuntimeError):
    pass


def connect(dsn: str | None):
    try:
        import oracledb
    except ImportError:
        raise DatabaseError("database commands need python-oracledb: pip install 'ords2apx[db]'") from None
    dsn = dsn or os.environ.get(ENV_DSN)
    if not dsn:
        raise DatabaseError(f"no connection: pass --dsn user/password@host:port/service or set {ENV_DSN}")
    credentials, _, connect_string = dsn.rpartition("@")
    if not credentials:
        raise DatabaseError("--dsn must look like user/password@host:port/service")
    user, _, password = credentials.partition("/")
    if not password:
        password = getpass.getpass(f"Password for {user}: ")
    try:
        return oracledb.connect(user=user, password=password, dsn=connect_string)
    except oracledb.Error as e:
        raise DatabaseError(f"could not connect as {user}@{connect_string}: {e}") from None


def export_schema(conn) -> str:
    """The schema's ORDS definitions as an export script (ORDS_EXPORT.EXPORT_SCHEMA)."""
    import oracledb
    # the no-argument form leaves out OAuth clients and JWT profiles; ask for
    # everything, falling back for ORDS versions without these flags
    calls = [
        "begin :out := ords_export.export_schema(p_include_modules => true, p_include_privileges => true,"
        " p_include_roles => true, p_include_oauth => true, p_include_rest_objects => true,"
        " p_include_jwt_profiles => true, p_include_enable_schema => true, p_export_date => false); end;",
        "begin :out := ords_export.export_schema(); end;",
    ]
    with conn.cursor() as cur:
        out = cur.var(oracledb.DB_TYPE_CLOB)
        for n, call in enumerate(calls):
            try:
                cur.execute(call, out=out)
                break
            except oracledb.DatabaseError as e:
                if n == len(calls) - 1 or "PLS-00306" not in str(e):
                    raise DatabaseError(f"ORDS_EXPORT.EXPORT_SCHEMA failed (is ORDS installed and the schema "
                                        f"enabled?): {e}") from None
        value = out.getvalue()
        return value.read() if value is not None else ""


def user_components(doc: Document) -> Document:
    """Drop what ORDS maintains itself and export-only statements."""
    def keep(c: Component) -> bool:
        if c.kind in ("restRole", "restPrivilege") and (c.id or "").startswith(SYSTEM_PREFIXES):
            return False
        if c.kind == "plsql":
            code = c.get("code")
            text = getattr(code, "text", getattr(code, "value", "")).lstrip().upper()
            return not text.startswith(EXPORT_ONLY)
        return True
    return Document([c for c in doc.components if keep(c)], doc.header)


def remote_document(conn) -> Document:
    return user_components(sql_to_document(export_schema(conn), "the database"))


# --- plan ---------------------------------------------------------------------


@dataclass
class Change:
    action: str  # add | change | remove
    kind: str
    id: str | None
    local: Component | None = None
    remote: Component | None = None
    diff: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.kind} {self.id}" if self.id is not None else self.kind


def _key(c: Component) -> tuple[str, str]:
    if c.kind == "plsql":
        code = c.get("code")
        return c.kind, code.text if isinstance(code, (apx.Code,)) else getattr(code, "value", "")
    return c.kind, c.id or ""


def _canonical(c: Component, like: Component | None, schema: Schema) -> Component:
    """A copy for comparison: references as names, unordered arrays sorted, values
    equal to their default dropped, and generated values dropped when `like`
    (the other side) leaves them out."""
    spec = schema.components.get(c.kind)
    other = {(g, k): True for g, props in _props_of(like) for k in props} if like is not None else {}

    def norm(group: str | None, key: str, v):
        p = spec.property(key, group) if spec else None
        if p is not None and p.default is not None and _scalar_text(v) == p.default:
            return None
        if p is not None and p.generated and like is not None and (group, key) not in other:
            return None
        if isinstance(v, apx.Array):
            items = [apx.Text(i.target) if isinstance(i, apx.Reference) else i for i in v.items]
            if key in UNORDERED:
                items.sort(key=_scalar_text)
            return apx.Array(tuple(items))
        return v

    copy = Component(c.kind, c.id)
    for group, props in _props_of(c):
        for key, v in props.items():
            copy.set(key, norm(group, key, v), group)
    others = {(ch.kind, ch.id): ch for ch in like.children} if like is not None else {}
    copy.children = [_canonical(ch, others.get((ch.kind, ch.id)), schema) for ch in c.children]
    return copy


def _props_of(c: Component | None):
    if c is None:
        return []
    return [(None, c.props)] + [(g.name, g.props) for g in c.groups.values()]


def _scalar_text(v) -> str:
    match v:
        case apx.Text(value):
            return value
        case apx.Number(text):
            return text
        case apx.Boolean(value):
            return "true" if value else "false"
        case apx.Reference(target):
            return target
    return ""


def _text(c: Component, like: Component | None = None, schema: Schema | None = None) -> str:
    return apx.dumps(Document([_canonical(c, like, schema or default_schema())]))


def plan(local: list[Document], remote: Document) -> list[Change]:
    """Changes that make the database match the local project, in deployment order."""
    mine = {_key(c): c for c in merge(local).components}
    theirs = {_key(c): c for c in remote.components}
    changes: list[Change] = []
    for key, c in mine.items():
        other = theirs.get(key)
        if other is None:
            changes.append(Change("add", c.kind, c.id, local=c))
        elif _text(c, other) != _text(other, c):
            diff = list(difflib.unified_diff(_text(other, c).splitlines(), _text(c, other).splitlines(),
                                             "database", "local", lineterm="", n=2))[2:]
            changes.append(Change("change", c.kind, c.id, local=c, remote=other, diff=diff))
    for key, c in theirs.items():
        # plsql blocks in an export are things the converter does not model; never remove them
        if key not in mine and c.kind != "plsql":
            changes.append(Change("remove", c.kind, c.id, remote=c))

    def rank(ch: Change) -> tuple[int, int]:
        order = KIND_ORDER.index(ch.kind) if ch.kind in KIND_ORDER else len(KIND_ORDER)
        # remove privileges before the modules and roles they name
        return (0, -order) if ch.action == "remove" else (1, order)

    return sorted(changes, key=rank)


def format_plan(changes: list[Change], prune: bool) -> str:
    if not changes:
        return "No changes. The database matches the project."
    lines = []
    symbols = {"add": "+", "change": "~", "remove": "-"}
    for ch in changes:
        note = "" if ch.action != "remove" or prune else "   (kept; use --prune to remove)"
        lines.append(f"{symbols[ch.action]} {ch.label}{note}")
        lines += ["    " + d for d in ch.diff]
    counts = {a: sum(1 for c in changes if c.action == a) for a in ("add", "change", "remove")}
    lines.append("")
    lines.append(f"{counts['add']} to add, {counts['change']} to change, "
                 f"{counts['remove']} to remove" + ("" if prune or not counts["remove"] else " (skipped without --prune)"))
    return "\n".join(lines)


def change_calls(changes: list[Change], prune: bool) -> list[OrdsCall | str]:
    calls: list[OrdsCall | str] = []
    for ch in changes:
        if ch.action == "remove":
            if not prune:
                continue
            if ch.kind == "restEnabledObject":
                args = {"p_enabled": False, "p_object": ch.id}
                for key, param in (("schema", "p_schema"), ("type", "p_object_type")):
                    v = ch.remote.get(key)
                    if isinstance(v, apx.Text):
                        args[param] = v.value if key == "schema" else v.value.upper()
                calls.append(OrdsCall("ORDS.ENABLE_OBJECT", args))
            elif ch.kind in DELETE_CALLS:
                calls.append(_delete_call(ch.kind, ch.id))
            else:
                raise DatabaseError(f"don't know how to remove {ch.label}; remove it by hand or drop --prune")
        else:
            if ch.action == "change" and ch.kind in RECREATE:
                calls.append(_delete_call(ch.kind, ch.id))
            calls += document_to_calls(Document([ch.local]))
    return calls


def _delete_call(kind: str, cid: str | None) -> OrdsCall:
    proc, param = DELETE_CALLS[kind]
    return OrdsCall(proc, {param: cid} if param else {})


def deploy_block(changes: list[Change], prune: bool) -> str | None:
    calls = change_calls(changes, prune)
    return calls_to_block(calls) if calls else None


def execute(conn, block: str) -> None:
    import oracledb
    with conn.cursor() as cur:
        try:
            cur.execute(block)
        except oracledb.DatabaseError as e:
            conn.rollback()
            raise DatabaseError(f"deploy failed and was rolled back: {e}") from None
