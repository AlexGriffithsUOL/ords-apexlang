"""Mapping between ORDS API calls and APEXlang components.

The mapping comes from the grammar: each component names its ORDS procedure
and id parameter, and each property its ORDS parameter. With the bundled
grammar that gives

    ORDS.ENABLE_SCHEMA          restSchema
    ORDS.DEFINE_MODULE          restModule
      ORDS.DEFINE_TEMPLATE        template
        ORDS.DEFINE_HANDLER         handler
          ORDS.DEFINE_PARAMETER       parameter
    ORDS.CREATE_ROLE            restRole
    ORDS.DEFINE_PRIVILEGE       restPrivilege
    ORDS.ENABLE_OBJECT          restEnabledObject
    anything else               plsql (kept verbatim)

This module adds what a grammar cannot say: properties set by a companion
call (ORDS.SET_MODULE_ORIGINS_ALLOWED, ORDS.CREATE_PRIVILEGE_MAPPING), the
short names of handler source types, and which code property holds a
handler's source.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import cache

from . import apx
from .apx import Array, Boolean, Code, Component, Document, Number, Reference, Text
from .plsql import Call, Num, Raw, Script, literal
from .schema import Schema, default_schema


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Prop:
    param: str
    key: str
    kind: str  # text | code | num | bool | enum | list
    group: str | None = None
    lang: str = ""


@dataclass(frozen=True)
class Spec:
    proc: str
    kind: str
    id_param: str | None
    parent_params: tuple[str, ...]
    props: tuple[Prop, ...]

    def props_for_param(self, param: str) -> list[Prop]:
        return [p for p in self.props if p.param == param]

    def prop_for_key(self, key: str, group: str | None) -> Prop | None:
        return next((p for p in self.props if p.key == key and p.group == group), None)


RAW_KIND = "plsql"

KINDS = {"boolean": "bool", "number": "num", "text": "text", "code": "code", "names": "list", "enum": "enum",
         "value": "text"}

# properties ORDS sets with a separate call after the component's own
COMPANION_CALLS = {"p_origins_allowed": ("ORDS.SET_MODULE_ORIGINS_ALLOWED", "p_module_name")}
# list properties set with one call per item: param -> (procedure, owner param, item param)
LIST_COMPANION_CALLS = {("restOauthClient", "p_roles"): ("ORDS_SECURITY.GRANT_CLIENT_ROLE", "p_client_name", "p_role_name")}
# list properties whose names can be @references, and the kind they point to
REFERENCE_KINDS = {"roles": "restRole", "modules": "restModule", "privileges": "restPrivilege"}
# other procedures that define the same component as a grammar procedure
PROC_ALIASES = {
    "ORDS_SECURITY.REGISTER_CLIENT": "ORDS_SECURITY.IMPORT_CLIENT",
    "OAUTH.CREATE_CLIENT": "ORDS_SECURITY.IMPORT_CLIENT",
    "OAUTH.GRANT_CLIENT_ROLE": "ORDS_SECURITY.GRANT_CLIENT_ROLE",
    "OAUTH.CREATE_JWT_PROFILE": "ORDS_SECURITY.CREATE_JWT_PROFILE",
}
# VARCHAR2 parameters holding a comma-separated list, written as arrays
CSV_LIST_PARAMS = {"p_privilege_names"}

# handler source types that are not UPPER_SNAKE get readable names; the
# grammar lists the same names as the enum values of handler source.type
SOURCE_TYPES = {
    "json/collection": "collection",
    "json/item": "item",
    "json/query": "query",
    "json/query;type=single_row": "queryOneRow",
    "csv/query": "csvQuery",
    "plsql/block": "plsql",
    "resource/lob": "media",
    "mle/javascript": "mleJavascript",
}
SOURCE_TYPES_REVERSE = {v: k for k, v in SOURCE_TYPES.items()}
# enum values that are not UPPER_SNAKE, by (component, parameter)
ENUM_MAPS = {
    ("handler", "p_source_type"): SOURCE_TYPES,
    ("restOauthClient", "p_grant_type"): {"client_credentials": "clientCredentials",
                                          "authorization_code": "authorizationCode", "implicit": "implicit"},
}
# which code language holds a handler's p_source, by source type
SOURCE_LANG = {"plsql/block": "plsql", "mle/javascript": "javascript"}

UPPER_SNAKE_RE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z][A-Z0-9]*)*\Z")
CAMEL_RE = re.compile(r"[a-z][a-z0-9]*([A-Z][a-z0-9]*)*\Z")


@cache
def specs(schema: Schema) -> dict[str, Spec]:
    """ORDS mapping for every component of the schema that has a procedure."""
    out = {}
    for c in schema.components.values():
        if c.procedure is None:
            continue
        props = tuple(Prop(p.param, p.key, KINDS[p.type], p.group, p.lang)
                      for p in c.all_properties() if p.param)
        out[c.kind] = Spec(c.procedure, c.kind, c.id_param, schema.parent_params(c.kind), props)
    return out


def _children(schema: Schema) -> dict[str, list[str]]:
    return {k: c.children for k, c in schema.components.items()}


def to_camel(snake: str) -> str:
    head, *rest = snake.lower().split("_")
    return head + "".join(w.capitalize() for w in rest)


def to_snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


def param_to_key(param: str) -> str | None:
    """p_foo_bar -> fooBar, or None if that would not convert back exactly."""
    key = to_camel(param.removeprefix("p_"))
    return key if param.startswith("p_") and key_to_param(key) == param else None


def key_to_param(key: str) -> str:
    return "p_" + to_snake(key)


# --- ORDS values -> APEXlang values -----------------------------------------


def _to_apx_value(prop: Prop | None, value: object, component: str | None = None) -> apx.Value | None:
    if value is None:
        return None
    kind = prop.kind if prop else None
    if prop and prop.param in CSV_LIST_PARAMS and isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, bool):
        return Boolean(value)
    if isinstance(value, Num):
        return Number(value.text)
    if isinstance(value, list):
        return Array(tuple(Text(v) for v in value)) if value else None
    assert isinstance(value, str)
    if kind == "enum":
        mapped = ENUM_MAPS.get((component, prop.param), {})
        if value in mapped:
            return Text(mapped[value])
        if UPPER_SNAKE_RE.match(value):
            return Text(to_camel(value))
        return Text(value, quoted=True)
    if kind == "code" or "\n" in value:
        return Code(value, prop.lang if prop else "")
    if kind is None:
        # extra parameter: keep a string a string even if it looks like a number
        return Text(value, quoted=bool(apx.NUMBER_RE.match(value)) or value in ("true", "false"))
    return Text(value)


def _pick_prop(spec: Spec, param: str, args: dict) -> Prop | None:
    """Choose the property for a parameter that several properties map to."""
    props = spec.props_for_param(param)
    if len(props) <= 1:
        return props[0] if props else None
    lang = SOURCE_LANG.get(args.get("p_source_type"), "sql")
    return next((p for p in props if p.lang == lang), props[0])


def _call_to_component(spec: Spec, call: Call) -> Component | None:
    cid = call.args.get(spec.id_param) if spec.id_param else None
    if cid is not None and not isinstance(cid, str):
        return None
    c = Component(spec.kind, cid)
    order = {p.param: i for i, p in enumerate(spec.props)}
    for param, value in sorted(call.args.items(), key=lambda kv: order.get(kv[0], len(order))):
        if param == spec.id_param or param in spec.parent_params:
            continue
        prop = _pick_prop(spec, param, call.args)
        if prop is None:
            key = param_to_key(param)
            if key is None or isinstance(value, list):
                return None
            c.set(key, _to_apx_value(None, value))
        else:
            if (prop.kind == "list") != isinstance(value, list) and value is not None \
                    and not (prop.param in CSV_LIST_PARAMS and isinstance(value, str)):
                return None
            c.set(prop.key, _to_apx_value(prop, value, spec.kind), prop.group)
    return c


def _raw_component(text: str) -> Component:
    c = Component(RAW_KIND)
    c.set("code", Code(text, "plsql"))
    return c


def _with_banner(banner: str, header: list[str]) -> list[str]:
    """Put our banner on a header, replacing a banner from an earlier conversion."""
    if header and header[0].endswith(" by ords2apx"):
        header = header[1:]
    while header and header[0] == "":
        header = header[1:]
    return [banner] + ([""] + header if header else [])


# ORDS.DEFINE_SERVICE defines a module, template and handler in one call; the
# split of its parameters and the defaults are those its exports show
SERVICE_PARTS = {
    "ORDS.DEFINE_MODULE": {"p_module_name": "p_module_name", "p_base_path": "p_base_path",
                           "p_items_per_page": "p_items_per_page", "p_status": "p_status",
                           "p_module_comments": "p_comments"},
    "ORDS.DEFINE_TEMPLATE": {"p_module_name": "p_module_name", "p_pattern": "p_pattern", "p_etag_type": "p_etag_type",
                             "p_etag_query": "p_etag_query", "p_template_comments": "p_comments"},
    "ORDS.DEFINE_HANDLER": {"p_module_name": "p_module_name", "p_pattern": "p_pattern", "p_method": "p_method",
                            "p_source_type": "p_source_type", "p_source": "p_source",
                            "p_mimes_allowed": "p_mimes_allowed", "p_handler_comments": "p_comments"},
}
SERVICE_DEFAULTS = {"p_method": "GET", "p_source_type": "json/collection"}


def _expand_services(statements: list) -> list:
    out = []
    for stmt in statements:
        known = {p for part in SERVICE_PARTS.values() for p in part}
        if isinstance(stmt, Call) and stmt.name == "ORDS.DEFINE_SERVICE" and set(stmt.args) <= known:
            args = {**SERVICE_DEFAULTS, **stmt.args}
            for proc, params in SERVICE_PARTS.items():
                out.append(Call(proc, {new: args[old] for old, new in params.items() if old in args}, stmt.text))
        else:
            out.append(stmt)
    return out


def script_to_document(script: Script, source_name: str | None = None, schema: Schema | None = None) -> Document:
    schema = schema or default_schema()
    by_proc = {s.proc: s for s in specs(schema).values()}
    children = _children(schema)
    doc = Document()
    doc.header = _with_banner(f"Converted from {source_name} by ords2apx" if source_name
                              else "Converted by ords2apx", script.header)

    top: dict[tuple[str, str | None], Component] = {}

    def find_parent(call: Call, spec: Spec) -> Component | None:
        """Find the component a nested call attaches to (None if missing)."""
        root_kind = next(k for k, s in specs(schema).items() if not s.parent_params and spec.kind in _descendants(k))
        node = top.get((root_kind, call.args.get(spec.parent_params[0])))
        kind = root_kind
        for param in spec.parent_params[1:]:
            if node is None:
                return None
            want = call.args.get(param)
            kind = next(k for k in children[kind] if specs(schema)[k].id_param == param)
            node = next((ch for ch in node.children if ch.kind == kind and ch.id == want), None)
        return node

    @cache
    def _descendants(kind: str) -> set[str]:
        out = set(children[kind])
        for child in children[kind]:
            out |= _descendants(child)
        return out

    statements = _expand_services(script.statements)
    for stmt in statements:
        if isinstance(stmt, Raw):
            doc.components.append(_raw_component(stmt.text))
            continue
        call = Call(PROC_ALIASES.get(stmt.name, stmt.name), stmt.args, stmt.text)
        handled = False
        spec = by_proc.get(call.name)
        list_companion = next(((kind, param, owner, item) for (kind, param), (proc, owner, item)
                               in LIST_COMPANION_CALLS.items() if proc == call.name), None)
        companion = next(((param, owner) for param, (proc, owner) in COMPANION_CALLS.items() if proc == call.name), None)
        if spec is not None:
            comp = _call_to_component(spec, call)
            if comp is not None and spec.parent_params:
                if all(isinstance(call.args.get(p), str) for p in spec.parent_params):
                    parent = find_parent(call, spec)
                    if parent is not None and not any(ch.kind == comp.kind and ch.id == comp.id for ch in parent.children):
                        parent.children.append(comp)
                        handled = True
            elif comp is not None and (spec.kind, comp.id) not in top:
                top[(spec.kind, comp.id)] = comp
                doc.components.append(comp)
                handled = True
        elif companion is not None and set(call.args) == {companion[0], companion[1]}:
            param, owner_param = companion
            owner_spec = next(s for s in specs(schema).values() if s.id_param == owner_param and not s.parent_params)
            owner = top.get((owner_spec.kind, call.args[owner_param]))
            prop = owner_spec.props_for_param(param)
            value = call.args[param]
            if owner is not None and prop and isinstance(value, str) and owner.get(prop[0].key, prop[0].group) is None:
                owner.set(prop[0].key, _to_apx_value(prop[0], value), prop[0].group)
                handled = True
        elif list_companion is not None and set(call.args) == {list_companion[2], list_companion[3]}:
            kind, param, owner_param, item_param = list_companion
            owner = top.get((kind, call.args[owner_param]))
            item = call.args[item_param]
            prop = specs(schema)[kind].props_for_param(param)[0] if kind in specs(schema) else None
            if owner is not None and prop is not None and isinstance(item, str):
                old = owner.get(prop.key, prop.group)
                old = list(old.items) if isinstance(old, Array) else []
                owner.set(prop.key, Array(tuple(old) + (Text(item),)), prop.group)
                handled = True
        elif call.name == "ORDS.CREATE_PRIVILEGE_MAPPING" and set(call.args) in ({"p_privilege_name", "p_pattern"},
                                                                                 {"p_privilege_name", "p_patterns"}):
            priv = top.get(("restPrivilege", call.args["p_privilege_name"]))
            new = call.args.get("p_pattern") or call.args.get("p_patterns")
            new = [new] if isinstance(new, str) else new
            if priv is not None and isinstance(new, list) and all(isinstance(p, str) for p in new):
                old = priv.get("patterns")
                old = list(old.items) if isinstance(old, Array) else []
                priv.set("patterns", Array(tuple(old) + tuple(Text(p) for p in new)))
                handled = True
        if not handled:
            doc.components.append(_raw_component(call.text))

    # point at roles, modules and privileges defined in this file with @references
    for c in top.values():
        for key, kind in REFERENCE_KINDS.items():
            items = c.get(key)
            if isinstance(items, Array):
                c.set(key, Array(tuple(
                    Reference(i.value) if isinstance(i, Text) and (kind, i.value) in top and apx.IDENTIFIER_RE.match(i.value)
                    else i for i in items.items)))
    return doc


# --- APEXlang values -> ORDS values -----------------------------------------


def _err(c: Component, message: str) -> ConversionError:
    where = f"{c.kind} {c.id}" if c.id is not None else c.kind
    return ConversionError(f"line {c.line_no}: {where}: {message}")


def _from_apx_value(c: Component, prop: Prop | None, key: str, value: apx.Value) -> object:
    kind = prop.kind if prop else None
    if kind == "list":
        if not isinstance(value, Array):
            raise _err(c, f"'{key}' must be an array, e.g. {key}: [a b]")
        out = []
        for item in value.items:
            if isinstance(item, Reference):
                out.append(item.target)
            elif isinstance(item, Text):
                out.append(item.value)
            elif isinstance(item, Number):
                out.append(item.text)
            else:
                raise _err(c, f"'{key}' items must be names")
        return out
    if isinstance(value, Array):
        raise _err(c, f"'{key}' does not take an array")
    if isinstance(value, Reference):
        raise _err(c, f"'{key}' does not take a reference")
    if kind == "bool" and not isinstance(value, Boolean):
        raise _err(c, f"'{key}' must be true or false")
    if kind == "num" and not isinstance(value, Number):
        raise _err(c, f"'{key}' must be a number")
    if isinstance(value, Boolean):
        if kind not in (None, "bool"):
            raise _err(c, f"'{key}' does not take a boolean")
        return value.value
    if isinstance(value, Number):
        return Num(value.text) if kind in (None, "num") else value.text
    if isinstance(value, Code):
        return value.text
    assert isinstance(value, Text)
    if kind == "enum" and not value.quoted:
        mapped = ENUM_MAPS.get((c.kind, prop.param))
        if mapped:
            reverse = {v: k for k, v in mapped.items()}
            if value.value in reverse:
                return reverse[value.value]
            raise _err(c, f"unknown {key} '{value.value}' (expected one of {', '.join(reverse)}, "
                          "or a quoted ORDS value)")
        if not CAMEL_RE.match(value.value):
            raise _err(c, f"'{key}' value '{value.value}' is not a camelCase enum; quote it to pass it through")
        return to_snake(value.value).upper()
    return value.value


@dataclass
class OrdsCall:
    proc: str
    args: dict[str, object]  # ordered, values as plsql.literal() accepts


def _component_args(c: Component, spec: Spec, warn) -> dict[str, object]:
    args: dict[str, object] = {}
    seen: dict[str, str] = {}
    for gname, props in [(None, c.props)] + [(g.name, g.props) for g in c.groups.values()]:
        for key, value in props.items():
            label = f"{gname}.{key}" if gname else key
            prop = spec.prop_for_key(key, gname)
            if prop is None:
                if gname is not None:
                    raise _err(c, f"unknown property '{label}'")
                if not CAMEL_RE.match(key):
                    raise _err(c, f"property '{key}' cannot be mapped to an ORDS parameter")
                warn(_err(c, f"unknown property '{key}' passed through as {key_to_param(key)}"))
                param = key_to_param(key)
            else:
                param = prop.param
            if param in seen:
                raise _err(c, f"'{label}' and '{seen[param]}' both set {param}")
            seen[param] = label
            args[param] = _from_apx_value(c, prop, label, value)
            if param in CSV_LIST_PARAMS and isinstance(args[param], list):
                args[param] = ",".join(args[param])
    # canonical argument order: as listed in the grammar, extras after
    order: dict[str, int] = {}
    for p in spec.props:
        order.setdefault(p.param, len(order))
    return dict(sorted(args.items(), key=lambda kv: order.get(kv[0], len(order))))


def _default_warn(e: Exception) -> None:
    print(f"warning: {e}", file=sys.stderr)


def document_to_calls(doc: Document, warn=_default_warn, schema: Schema | None = None) -> list[OrdsCall | str]:
    """Flatten a document into ORDS calls (or raw PL/SQL strings), in order."""
    schema = schema or default_schema()
    all_specs = specs(schema)
    out: list[OrdsCall | str] = []

    def visit(c: Component, parent_args: dict[str, object], parent_kind: str | None) -> None:
        if c.kind == RAW_KIND:
            code = c.get("code")
            if parent_kind is not None or not isinstance(code, (Code, Text)) or set(c.props) != {"code"} or c.groups or c.children:
                raise _err(c, "a plsql component is top level and holds exactly one property, code")
            out.append(code.text if isinstance(code, Code) else code.value)
            return
        spec = all_specs.get(c.kind)
        if spec is None:
            raise _err(c, f"unknown component kind '{c.kind}' (expected one of {', '.join(schema.top_level)})")
        expected_parent = schema.components[c.kind].parent
        if expected_parent != parent_kind:
            if parent_kind is None:
                raise _err(c, f"must be nested inside {expected_parent}")
            raise _err(c, f"cannot be nested inside {parent_kind}; "
                          + (f"it belongs in {expected_parent}" if expected_parent else "it is top level"))
        if c.id is None and schema.components[c.kind].id == "required":
            raise _err(c, f"needs an id, e.g. {c.kind} my-name (")
        args = dict(parent_args)
        if c.id is not None and spec.id_param:
            args[spec.id_param] = c.id.upper() if c.kind == "handler" else c.id
        own = _component_args(c, spec, warn)
        companions = {p: own.pop(p) for p in list(own) if p in COMPANION_CALLS}
        list_companions = {p: own.pop(p) for p in list(own) if (c.kind, p) in LIST_COMPANION_CALLS}
        args.update(own)
        out.append(OrdsCall(spec.proc, args))
        for param, value in companions.items():
            proc, owner_param = COMPANION_CALLS[param]
            out.append(OrdsCall(proc, {owner_param: args[owner_param], param: value}))
        for param, items in list_companions.items():
            proc, owner_param, item_param = LIST_COMPANION_CALLS[(c.kind, param)]
            out.extend(OrdsCall(proc, {owner_param: c.id, item_param: item}) for item in items)
        child_ids: set[tuple[str, str | None]] = set()
        for child in c.children:
            key = (child.kind, child.id.upper() if child.kind == "handler" and child.id else child.id)
            if key in child_ids:
                raise _err(child, "duplicate id")
            child_ids.add(key)
            child_spec = all_specs.get(child.kind)
            visit(child, {p: args[p] for p in (child_spec.parent_params if child_spec else ()) if p in args}, c.kind)

    seen: set[tuple[str, str | None]] = set()
    for c in doc.components:
        if c.kind != RAW_KIND:
            if (c.kind, c.id) in seen:
                raise _err(c, "duplicate id")
            seen.add((c.kind, c.id))
        visit(c, {}, None)
    return out


# --- writing the ORDS script ------------------------------------------------

ARRAY_VARS = {"p_roles": "l_roles", "p_modules": "l_modules", "p_patterns": "l_patterns"}
CODE_PARAMS = {"p_source", "p_etag_query"}


def _write_call(call: OrdsCall) -> list[str]:
    lines: list[str] = []
    arrays = [p for p, v in call.args.items() if isinstance(v, list)]
    for param in arrays:
        if param not in ARRAY_VARS:
            raise ConversionError(f"{call.proc}: {param} cannot take a list")
        for i, v in enumerate(call.args[param], 1):
            lines.append(f"  {ARRAY_VARS[param]}({i}) := {literal(v)};")
    if arrays:
        lines.append("")
    width = max((len(p) for p in call.args), default=0)
    parts = []
    for param, value in call.args.items():
        lead = f"      {param.ljust(width)} => "
        if isinstance(value, list):
            parts.append(lead + ARRAY_VARS[param])
        elif param in CODE_PARAMS and isinstance(value, str):
            parts.append(lead.rstrip() + "\n" + literal(value))
        else:
            parts.append(lead + literal(value))
    if parts:
        body = ",\n".join(parts)
        close = "\n      );" if body.endswith("'") and "\n" in parts[-1] else ");"
        lines.append(f"  {call.proc}(\n{body}{close}")
    else:
        lines.append(f"  {call.proc};")
    if arrays:
        lines.append("")
        lines += [f"  {ARRAY_VARS[p]}.DELETE;" for p in arrays]
    return lines


def calls_to_block(calls: list[OrdsCall | str]) -> str:
    """One anonymous PL/SQL block making the calls, ending in COMMIT."""
    out = ["DECLARE"]
    out += [f"  {v.ljust(10)} OWA.VC_ARR;" for v in ARRAY_VARS.values()]
    out += ["BEGIN"]
    for call in calls:
        out.append("")
        if isinstance(call, str):
            out.append("  " + call)
        else:
            out += _write_call(call)
    out += ["", "  COMMIT;", "END;"]
    return "\n".join(out)


def calls_to_script(calls: list[OrdsCall | str], header: list[str]) -> str:
    """A SQL*Plus / SQLcl script: header comments, SET DEFINE OFF, the block, '/'."""
    out = [f"-- {h}" if h else "--" for h in header]
    if out:
        out.append("")
    out += ["SET DEFINE OFF", "", calls_to_block(calls), "/", ""]
    return "\n".join(out)


# --- entry points -------------------------------------------------------------


def sql_to_document(sql: str, source_name: str | None = None, schema: Schema | None = None) -> Document:
    from .plsql import parse_script
    return script_to_document(parse_script(sql), source_name, schema)


def sql_to_apx(sql: str, source_name: str | None = None, schema: Schema | None = None) -> str:
    return apx.dumps(sql_to_document(sql, source_name, schema))


def documents_to_sql(docs: list[Document], banner: str, warn=_default_warn, schema: Schema | None = None) -> str:
    calls: list[OrdsCall | str] = []
    for doc in docs:
        calls += document_to_calls(doc, warn, schema)
    return calls_to_script(calls, _with_banner(banner, docs[0].header if len(docs) == 1 else []))


def apx_to_sql(text: str, source_name: str | None = None, warn=_default_warn, schema: Schema | None = None) -> str:
    return documents_to_sql([apx.loads(text)],
                            f"Generated from {source_name} by ords2apx" if source_name else "Generated by ords2apx",
                            warn, schema)
