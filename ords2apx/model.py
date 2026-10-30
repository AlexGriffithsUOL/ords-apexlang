"""Mapping between ORDS API calls and APEXlang components.

    ORDS.ENABLE_SCHEMA          restSchema
    ORDS.DEFINE_MODULE          restModule
      ORDS.DEFINE_TEMPLATE        template
        ORDS.DEFINE_HANDLER         handler
          ORDS.DEFINE_PARAMETER       parameter
    ORDS.CREATE_ROLE            restRole
    ORDS.DEFINE_PRIVILEGE       restPrivilege
    ORDS.ENABLE_OBJECT          restEnabledObject
    anything else               plsql (kept verbatim)

ORDS.SET_MODULE_ORIGINS_ALLOWED and ORDS.CREATE_PRIVILEGE_MAPPING are folded
into the module and privilege they modify.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from . import apx
from .apx import Array, Boolean, Code, Component, Document, Number, Reference, Text
from .plsql import Call, Num, Raw, Script, literal


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Prop:
    param: str
    key: str
    kind: str  # text | code | num | bool | enum | list
    group: str | None = None
    lang: str = ""
    values: tuple[str, ...] = ()  # known enum values; must match grammar/ords-apexlang.ebnf


@dataclass(frozen=True)
class Spec:
    proc: str
    kind: str
    id_param: str | None
    parent_params: tuple[str, ...]
    props: tuple[Prop, ...]

    def prop_for_param(self, param: str) -> Prop | None:
        return next((p for p in self.props if p.param == param), None)

    def prop_for_key(self, key: str, group: str | None) -> Prop | None:
        return next((p for p in self.props if p.key == key and p.group == group), None)


# handler source types that are not UPPER_SNAKE get readable names
SOURCE_TYPES = {
    "json/collection": "collection",
    "json/item": "item",
    "json/query": "query",
    "json/query;type=single_row": "queryOneRow",
    "csv/query": "csvQuery",
    "plsql/block": "plsql",
    "resource/lob": "media",
}
SOURCE_TYPES_REVERSE = {v: k for k, v in SOURCE_TYPES.items()}

COMMENTS = Prop("p_comments", "comments", "text")

SPECS = (
    Spec("ORDS.ENABLE_SCHEMA", "restSchema", "p_schema", (), (
        Prop("p_enabled", "enabled", "bool"),
        Prop("p_url_mapping_type", "type", "enum", "urlMapping", values=("basePath", "baseUrl")),
        Prop("p_url_mapping_pattern", "pattern", "text", "urlMapping"),
        Prop("p_auto_rest_auth", "autoRestAuth", "bool"),
    )),
    Spec("ORDS.DEFINE_MODULE", "restModule", "p_module_name", (), (
        Prop("p_base_path", "basePath", "text"),
        Prop("p_items_per_page", "itemsPerPage", "num"),
        Prop("p_status", "status", "enum", values=("published", "notPublished")),
        Prop("p_origins_allowed", "originsAllowed", "text"),
        COMMENTS,
    )),
    Spec("ORDS.DEFINE_TEMPLATE", "template", "p_pattern", ("p_module_name",), (
        Prop("p_priority", "priority", "num"),
        Prop("p_etag_type", "type", "enum", "etag", values=("hash", "query", "none")),
        Prop("p_etag_query", "query", "code", "etag", "sql"),
        COMMENTS,
    )),
    Spec("ORDS.DEFINE_HANDLER", "handler", "p_method", ("p_module_name", "p_pattern"), (
        Prop("p_source_type", "type", "enum", "source", values=tuple(SOURCE_TYPES.values())),
        Prop("p_items_per_page", "itemsPerPage", "num"),
        Prop("p_mimes_allowed", "mimesAllowed", "text"),
        COMMENTS,
        Prop("p_source", "sqlQuery", "code", "source", "sql"),
    )),
    Spec("ORDS.DEFINE_PARAMETER", "parameter", "p_name", ("p_module_name", "p_pattern", "p_method"), (
        Prop("p_bind_variable_name", "bindVariable", "text"),
        Prop("p_source_type", "source", "enum", values=("header", "response", "uri")),
        Prop("p_param_type", "type", "enum",
             values=("string", "int", "double", "boolean", "long", "timestamp", "resultset")),
        Prop("p_access_method", "accessMethod", "enum", values=("in", "out", "inout")),
        COMMENTS,
    )),
    Spec("ORDS.CREATE_ROLE", "restRole", "p_role_name", (), ()),
    Spec("ORDS.DEFINE_PRIVILEGE", "restPrivilege", "p_privilege_name", (), (
        Prop("p_label", "label", "text"),
        Prop("p_description", "description", "text"),
        Prop("p_roles", "roles", "list"),
        Prop("p_modules", "modules", "list"),
        Prop("p_patterns", "patterns", "list"),
        COMMENTS,
    )),
    Spec("ORDS.ENABLE_OBJECT", "restEnabledObject", "p_object", (), (
        Prop("p_schema", "schema", "text"),
        Prop("p_object_type", "type", "enum", values=("table", "view", "package", "procedure", "function")),
        Prop("p_object_alias", "alias", "text"),
        Prop("p_enabled", "enabled", "bool"),
        Prop("p_auto_rest_auth", "autoRestAuth", "bool"),
        COMMENTS,
    )),
)

SPEC_BY_PROC = {s.proc: s for s in SPECS}
SPEC_BY_KIND = {s.kind: s for s in SPECS}
CHILD_KIND = {"restModule": "template", "template": "handler", "handler": "parameter"}
RAW_KIND = "plsql"


UPPER_SNAKE_RE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z][A-Z0-9]*)*\Z")
CAMEL_RE = re.compile(r"[a-z][a-z0-9]*([A-Z][a-z0-9]*)*\Z")


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


def _to_apx_value(prop: Prop | None, value: object) -> apx.Value | None:
    if value is None:
        return None
    kind = prop.kind if prop else None
    if isinstance(value, bool):
        return Boolean(value)
    if isinstance(value, Num):
        return Number(value.text)
    if isinstance(value, list):
        return Array(tuple(Text(v) for v in value)) if value else None
    assert isinstance(value, str)
    if kind == "enum":
        if prop.param == "p_source_type" and value in SOURCE_TYPES:
            return Text(SOURCE_TYPES[value])
        if UPPER_SNAKE_RE.match(value):
            return Text(to_camel(value))
        return Text(value, quoted=True)
    if kind == "code" or "\n" in value:
        return Code(value, prop.lang if prop else "")
    if kind is None:
        # extra parameter: keep a string a string even if it looks like a number
        return Text(value, quoted=bool(apx.NUMBER_RE.match(value)) or value in ("true", "false"))
    return Text(value)


def _call_to_component(spec: Spec, call: Call) -> Component | None:
    cid = call.args.get(spec.id_param) if spec.id_param else None
    if cid is not None and not isinstance(cid, str):
        return None
    c = Component(spec.kind, cid)
    order = {p.param: i for i, p in enumerate(spec.props)}
    for param, value in sorted(call.args.items(), key=lambda kv: order.get(kv[0], len(order))):
        if param == spec.id_param or param in spec.parent_params:
            continue
        prop = spec.prop_for_param(param)
        if prop is None:
            key = param_to_key(param)
            if key is None or isinstance(value, list):
                return None
            c.set(key, _to_apx_value(None, value))
        else:
            if (prop.kind == "list") != isinstance(value, list) and value is not None:
                return None
            c.set(prop.key, _to_apx_value(prop, value), prop.group)
    if spec.kind == "handler":
        src_type = call.args.get("p_source_type")
        if src_type == "plsql/block" and c.get("sqlQuery", "source") is not None:
            code = c.groups["source"].props.pop("sqlQuery")
            c.set("plsqlCode", Code(code.text if isinstance(code, Code) else code.value, "plsql"), "source")
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


def script_to_document(script: Script, source_name: str | None = None) -> Document:
    doc = Document()
    doc.header = _with_banner(f"Converted from {source_name} by ords2apx" if source_name
                              else "Converted by ords2apx", script.header)

    modules: dict[str, Component] = {}
    privileges: dict[str, Component] = {}
    top_ids: set[tuple[str, str | None]] = set()

    def find(call: Call, spec: Spec) -> Component | None:
        """Find the parent component a call attaches to (None if missing)."""
        node = modules.get(call.args.get("p_module_name"))
        for param, kind in zip(spec.parent_params[1:], ("template", "handler")):
            if node is None:
                return None
            want = call.args.get(param)
            node = next((ch for ch in node.children if ch.kind == kind and ch.id == want), None)
        return node

    for stmt in script.statements:
        if isinstance(stmt, Raw):
            doc.components.append(_raw_component(stmt.text))
            continue
        call = stmt
        handled = False
        spec = SPEC_BY_PROC.get(call.name)
        if spec is not None:
            comp = _call_to_component(spec, call)
            if comp is not None and spec.parent_params:
                if not all(isinstance(call.args.get(p), str) for p in spec.parent_params):
                    comp = None
                else:
                    parent = find(call, spec)
                    if parent is not None and not any(ch.id == comp.id for ch in parent.children):
                        parent.children.append(comp)
                        handled = True
            elif comp is not None and (spec.kind, comp.id) not in top_ids:
                top_ids.add((spec.kind, comp.id))
                doc.components.append(comp)
                if spec.kind == "restModule":
                    modules[comp.id] = comp
                elif spec.kind == "restPrivilege":
                    privileges[comp.id] = comp
                handled = True
        elif call.name == "ORDS.SET_MODULE_ORIGINS_ALLOWED" and set(call.args) == {"p_module_name", "p_origins_allowed"}:
            module = modules.get(call.args["p_module_name"])
            origins = call.args["p_origins_allowed"]
            if module is not None and isinstance(origins, str) and module.get("originsAllowed") is None:
                module.set("originsAllowed", Text(origins))
                handled = True
        elif call.name == "ORDS.CREATE_PRIVILEGE_MAPPING" and set(call.args) in ({"p_privilege_name", "p_pattern"},
                                                                                 {"p_privilege_name", "p_patterns"}):
            priv = privileges.get(call.args["p_privilege_name"])
            new = call.args.get("p_pattern") or call.args.get("p_patterns")
            new = [new] if isinstance(new, str) else new
            if priv is not None and isinstance(new, list) and all(isinstance(p, str) for p in new):
                old = priv.get("patterns")
                old = list(old.items) if isinstance(old, Array) else []
                priv.set("patterns", Array(tuple(old) + tuple(Text(p) for p in new)))
                handled = True
        if not handled:
            doc.components.append(_raw_component(call.text))

    # point privileges at modules defined in this file with @references
    for priv in privileges.values():
        mods = priv.get("modules")
        if isinstance(mods, Array):
            priv.set("modules", Array(tuple(
                Reference(m.value) if isinstance(m, Text) and m.value in modules else m for m in mods.items)))
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
        if prop.param == "p_source_type" and c.kind == "handler":
            if value.value in SOURCE_TYPES_REVERSE:
                return SOURCE_TYPES_REVERSE[value.value]
            raise _err(c, f"unknown source type '{value.value}' (expected one of "
                          f"{', '.join(SOURCE_TYPES_REVERSE)}, or a quoted ORDS value)")
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
            if spec.kind == "handler" and gname == "source" and key == "plsqlCode":
                key = "sqlQuery"
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
    # canonical argument order: as listed in the spec, extras after
    order = {p.param: i for i, p in enumerate(spec.props)}
    return dict(sorted(args.items(), key=lambda kv: order.get(kv[0], len(order))))


def document_to_calls(doc: Document, warn=lambda e: print(f"warning: {e}", file=sys.stderr)) -> list[OrdsCall | str]:
    """Flatten a document into ORDS calls (or raw PL/SQL strings), in order."""
    out: list[OrdsCall | str] = []

    def visit(c: Component, parent_args: dict[str, object]) -> None:
        if c.kind == RAW_KIND:
            code = c.get("code")
            if not isinstance(code, (Code, Text)) or set(c.props) != {"code"} or c.groups or c.children:
                raise _err(c, "a plsql component holds exactly one property, code")
            out.append(code.text if isinstance(code, Code) else code.value)
            return
        spec = SPEC_BY_KIND.get(c.kind)
        if spec is None:
            raise _err(c, f"unknown component kind '{c.kind}' (expected one of "
                          f"{', '.join(k for k in SPEC_BY_KIND if not SPEC_BY_KIND[k].parent_params)}, {RAW_KIND})")
        expected_parent = next((k for k, v in CHILD_KIND.items() if v == c.kind), None)
        if bool(spec.parent_params) != bool(parent_args):
            raise _err(c, f"must be nested inside {expected_parent}" if expected_parent else "must be top level")
        if c.id is None and spec.id_param not in ("p_schema",):
            raise _err(c, f"needs an id, e.g. {c.kind} my-name (")
        args = dict(parent_args)
        if c.id is not None:
            args[spec.id_param] = c.id.upper() if c.kind == "handler" else c.id
        own = _component_args(c, spec, warn)
        origins = own.pop("p_origins_allowed", None)
        args.update(own)
        out.append(OrdsCall(spec.proc, args))
        if origins is not None:
            out.append(OrdsCall("ORDS.SET_MODULE_ORIGINS_ALLOWED",
                                {"p_module_name": c.id, "p_origins_allowed": origins}))
        child_kind = CHILD_KIND.get(c.kind)
        child_ids: set[str] = set()
        for child in c.children:
            if child.kind != child_kind:
                raise _err(child, f"cannot be nested inside {c.kind}"
                           + (f" (only {child_kind})" if child_kind else ""))
            if child.id in child_ids:
                raise _err(child, "duplicate id")
            child_ids.add(child.id)
            visit(child, {p: args[p] for p in SPEC_BY_KIND[child_kind].parent_params})

    seen: set[tuple[str, str | None]] = set()
    for c in doc.components:
        if c.kind != RAW_KIND:
            if (c.kind, c.id) in seen:
                raise _err(c, "duplicate id")
            seen.add((c.kind, c.id))
        visit(c, {})
    return out


# --- writing the ORDS script ------------------------------------------------

ARRAY_VARS = {"p_roles": "l_roles", "p_modules": "l_modules", "p_patterns": "l_patterns"}


def _write_call(call: OrdsCall) -> list[str]:
    lines: list[str] = []
    arrays = [p for p, v in call.args.items() if isinstance(v, list)]
    for param in arrays:
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
        elif param in ("p_source", "p_etag_query") and isinstance(value, str):
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


def calls_to_script(calls: list[OrdsCall | str], header: list[str]) -> str:
    out = [f"-- {h}" if h else "--" for h in header]
    if out:
        out.append("")
    out += ["SET DEFINE OFF", "", "DECLARE"]
    out += [f"  {v.ljust(10)} OWA.VC_ARR;" for v in ARRAY_VARS.values()]
    out += ["BEGIN"]
    for call in calls:
        out.append("")
        if isinstance(call, str):
            out.append("  " + call)
        else:
            out += _write_call(call)
    out += ["", "  COMMIT;", "END;", "/", ""]
    return "\n".join(out)


# --- entry points -------------------------------------------------------------


def sql_to_apx(sql: str, source_name: str | None = None) -> str:
    from .plsql import parse_script
    return apx.dumps(script_to_document(parse_script(sql), source_name))


def apx_to_sql(text: str, source_name: str | None = None, warn=None) -> str:
    doc = apx.loads(text)
    calls = document_to_calls(doc, warn) if warn else document_to_calls(doc)
    header = _with_banner(f"Generated from {source_name} by ords2apx" if source_name
                          else "Generated by ords2apx", doc.header)
    return calls_to_script(calls, header)
