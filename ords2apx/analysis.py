"""Editor features over a parsed .ords.apx document and the grammar's schema:
diagnostics, completion, hover, go to definition, symbols and formatting.

Positions are 0-based (line, character), as in the Language Server Protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import apx
from .apx import Array, Component, Document, Group, Problem, Reference, Span
from .schema import ComponentSpec, PropertySpec, Schema

CAMEL_RE = re.compile(r"[a-z][a-z0-9]*([A-Z][a-z0-9]*)*\Z")
# property -> component kind its @references point to
REFERENCE_KINDS = {"roles": "restRole", "modules": "restModule"}


def to_param(key: str) -> str:
    return "p_" + re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(word: str, options) -> str:
    scored = [(_distance(word.lower(), o.lower()), o) for o in options]
    if not scored:
        return ""
    score, best = min(scored)
    return f" Did you mean '{best}'?" if score <= max(2, len(word) // 3) else ""


def reference_targets(doc: Document) -> dict[str, Component]:
    """Top-level components a @reference can point to, by id."""
    out: dict[str, Component] = {}
    for c in doc.components:
        if c.id is not None:
            out.setdefault(c.id, c)
    return out


# --- diagnostics ------------------------------------------------------------


def _check_value(key: str, value: apx.Value, spec: PropertySpec, targets: dict[str, Component], out: list[Problem]):
    def err(message: str, span: Span | None = None):
        out.append(Problem(message, span or value.span))

    match spec.type:
        case "boolean":
            if not isinstance(value, apx.Boolean):
                err(f"'{key}' must be true or false")
        case "number":
            if not isinstance(value, apx.Number):
                err(f"'{key}' must be a number")
        case "enum":
            if isinstance(value, apx.Text) and value.quoted:
                return
            text = value.value if isinstance(value, apx.Text) else getattr(value, "text", str(value))
            if not isinstance(value, apx.Text) or value.value not in spec.values:
                err(f"'{text}' is not a valid {key}: expected {', '.join(spec.values)} (or a \"quoted\" ORDS value)."
                    + _suggest(str(text), spec.values))
        case "names":
            if not isinstance(value, Array):
                err(f"'{key}' must be an array, e.g. {key}: [a b]")
                return
            want = REFERENCE_KINDS.get(key)
            for item in value.items:
                if isinstance(item, Reference):
                    target = targets.get(item.target)
                    if target is None:
                        out.append(Problem(f"no component named '{item.target}' in this project", item.span, "warning"))
                    elif want and target.kind != want:
                        out.append(Problem(f"'{item.target}' is a {target.kind}, not a {want}", item.span, "warning"))
                elif isinstance(item, apx.Boolean):
                    err(f"'{'true' if item.value else 'false'}' is not a name; quote it", item.span)
        case "text" | "code":
            if isinstance(value, Array):
                err(f"'{key}' takes text, not an array")
            elif isinstance(value, Reference):
                err(f"'{key}' takes text, not a reference")


def _check_properties(props: dict, info: dict, specs: dict[str, PropertySpec], where: str, pass_through: bool,
                      targets: dict[str, Component], out: list[Problem]) -> None:
    for key, value in props.items():
        key_span = info[key].key_span if key in info else None
        spec = specs.get(key)
        if spec is not None:
            _check_value(key, value, spec, targets, out)
        elif pass_through and CAMEL_RE.match(key):
            out.append(Problem(f"'{key}' is not a known {where} property; it will be passed to ORDS as "
                               f"{to_param(key)}." + _suggest(key, specs), key_span, "warning"))
        else:
            out.append(Problem(f"unknown {where} property '{key}'." + _suggest(key, specs), key_span))


def validate(doc: Document, schema: Schema, project: dict[str, Component] | None = None) -> list[Problem]:
    """Problems in a document. `project` adds @reference targets from other files."""
    out = list(doc.problems)
    targets = {**(project or {}), **reference_targets(doc)}

    def check(c: Component, parent: ComponentSpec | None) -> None:
        spec = schema.components.get(c.kind)
        allowed = parent.children if parent else schema.top_level
        if spec is None:
            out.append(Problem(f"unknown component '{c.kind}'." + _suggest(c.kind, allowed), c.kind_span))
            return
        if c.kind not in allowed:
            if parent:
                msg = f"'{c.kind}' cannot be nested inside {parent.kind}" + (
                    f" (allowed: {', '.join(parent.children)})" if parent.children else "")
            else:
                msg = f"'{c.kind}' must be nested inside {spec.parent}"
            out.append(Problem(msg, c.kind_span))
        if spec.id == "required" and c.id is None:
            out.append(Problem(f"{c.kind} needs an id ({spec.id_param or 'name'}), e.g. {c.kind} name (", c.kind_span))
        elif spec.id == "none" and c.id is not None:
            out.append(Problem(f"{c.kind} does not take an id", c.id_span))
        _check_properties(c.props, c.info, spec.properties, c.kind, spec.pass_through, targets, out)
        for g in c.groups.values():
            gspec = spec.groups.get(g.name)
            if gspec is None:
                listed = f" (groups: {', '.join(spec.groups)})." if spec.groups else "."
                out.append(Problem(f"unknown {c.kind} group '{g.name}'{listed}" + _suggest(g.name, spec.groups),
                                   g.name_span))
                continue
            _check_properties(g.props, g.info, gspec.properties, f"{c.kind} {g.name}", False, targets, out)
        # several properties can map to one ORDS parameter (sqlQuery, plsqlCode, ...)
        params: dict[str, str] = {}
        for gname, props, info in [(None, c.props, c.info)] + [(g.name, g.props, g.info) for g in c.groups.values()]:
            for key in props:
                p = spec.property(key, gname)
                if p is None or p.param is None:
                    continue
                if p.param in params:
                    out.append(Problem(f"'{key}' and '{params[p.param]}' both set {p.param}; keep one",
                                       info[key].key_span if key in info else c.kind_span))
                params[p.param] = key
        seen: set[tuple[str, str]] = set()
        for child in c.children:
            check(child, spec)
            if child.id is None:
                continue
            key = (child.kind, child.id.upper() if child.kind == "handler" else child.id)
            if key in seen:
                out.append(Problem(f"duplicate {child.kind} '{child.id}'", child.id_span))
            seen.add(key)

    seen_top: set[tuple[str, str | None]] = set()
    for c in doc.components:
        check(c, None)
        if c.id is None and c.kind != "restSchema":
            continue
        if (c.kind, c.id) in seen_top:
            out.append(Problem(f"duplicate {c.kind} '{c.id or ''}'", c.id_span or c.kind_span))
        seen_top.add((c.kind, c.id))
    return out


# --- cursor context -----------------------------------------------------------


@dataclass
class Context:
    type: str  # top | component | group | value | code
    component: Component | None = None
    group: Group | None = None
    key: str | None = None
    in_array: bool = False


def _container_at(components: list[Component], line: int) -> Component | None:
    for c in components:
        if c.kind_span.line < line <= c.end_line:
            return _container_at(c.children, line) or c
    return None


def context_at(doc: Document, line: int, character: int) -> Context:
    if any(f.start < line < f.end for f in doc.fences):
        return Context("code")
    text = doc.lines[line][:character] if line < len(doc.lines) else ""
    component = _container_at(doc.components, line)
    if component is None:
        return Context("top")
    # an unclosed group ends on its last content line rather than on a '}'
    group = next((g for g in component.groups.values()
                  if g.start_line < line and (line < g.end_line or (line == g.end_line and doc.lines[line].strip() != "}"))),
                 None)
    if m := re.match(r"\s*([A-Za-z][A-Za-z0-9]*):\s(.*)\Z", text):
        prefix = m.group(2)
        return Context("value", component, group, m.group(1), "[" in prefix and "]" not in prefix)
    return Context("group" if group else "component", component, group)


# --- completion ----------------------------------------------------------------


@dataclass
class Completion:
    label: str
    kind: str  # component | property | group | value | reference
    insert_text: str  # snippet syntax; \t is one indentation level
    detail: str | None = None
    documentation: str | None = None
    sort_text: str | None = None


def property_doc(p: PropertySpec) -> str:
    parts = [p.description]
    if p.param:
        parts.append(f"ORDS parameter: `{p.param}`")
    if p.type == "enum":
        parts.append("Values: " + ", ".join(f"`{v}`" for v in p.values) + ", or a quoted ORDS value")
    if p.default is not None:
        parts.append(f"Default: `{p.default}`")
    elif p.generated:
        parts.append("Filled in by ORDS when left out")
    return "\n\n".join(x for x in parts if x)


def component_doc(c: ComponentSpec) -> str:
    parts = [c.description]
    if c.procedure:
        parts.append(f"Generates `{c.procedure}`" + (f"; the id is `{c.id_param}`." if c.id_param else "."))
    if c.children:
        parts.append("Contains: " + ", ".join(f"`{k}`" for k in c.children))
    return "\n\n".join(x for x in parts if x)


def _property_completion(p: PropertySpec) -> Completion:
    if p.type == "code":
        text = f"{p.key}:\n\t```{p.lang}\n\t$0\n\t```"
    elif p.type == "enum":
        text = f"{p.key}: ${{1|{','.join(p.values)}|}}"
    elif p.type == "boolean":
        text = f"{p.key}: ${{1|true,false|}}"
    elif p.type == "names":
        text = f"{p.key}: [$1]"
    else:
        text = f"{p.key}: $0"
    return Completion(p.key, "property", text, p.param, property_doc(p))


def _component_completion(spec: ComponentSpec) -> Completion:
    cid = f" ${{1:{(spec.id_param or 'p_name').removeprefix('p_')}}}" if spec.id == "required" else ""
    code = next((p for p in spec.properties.values() if p.type == "code"), None)
    body = f"\t{code.key}:\n\t\t```{code.lang}\n\t\t$0\n\t\t```" if spec.id == "none" and code else "\t$0"
    return Completion(spec.kind, "component", f"{spec.kind}{cid} (\n{body}\n)", spec.procedure, component_doc(spec))


def completions(doc: Document, schema: Schema, line: int, character: int) -> list[Completion]:
    ctx = context_at(doc, line, character)
    if ctx.type == "code":
        return []
    if ctx.type == "top":
        return [_component_completion(schema.components[k]) for k in schema.top_level]
    spec = schema.components.get(ctx.component.kind)
    if spec is None:
        return []
    if ctx.type == "component":
        out = [Completion(**{**_property_completion(p).__dict__, "sort_text": "1" + p.key})
               for p in spec.properties.values() if p.key not in ctx.component.props]
        for g in spec.groups.values():
            if g.name not in ctx.component.groups:
                out.append(Completion(g.name, "group", f"{g.name} {{\n\t$0\n}}",
                                      "group: " + ", ".join(g.properties), sort_text="2" + g.name))
        out += [Completion(**{**_component_completion(schema.components[k]).__dict__, "sort_text": "3" + k})
                for k in spec.children]
        return out
    if ctx.type == "group":
        g = spec.groups.get(ctx.group.name)
        return [_property_completion(p) for p in g.properties.values() if p.key not in ctx.group.props] if g else []
    # value
    p = spec.property(ctx.key, ctx.group.name if ctx.group else None)
    if p is None:
        return []
    if p.type == "enum":
        return [Completion(v, "value", v, p.param) for v in p.values]
    if p.type == "boolean":
        return [Completion(v, "value", v) for v in ("true", "false")]
    if p.type == "names" and ctx.in_array and ctx.key in REFERENCE_KINDS:
        return [Completion("@" + c.id, "reference", "@" + apx.format_id(c.id), c.kind)
                for c in doc.components if c.id is not None and c.kind == REFERENCE_KINDS[ctx.key]]
    return []


# --- hover, navigation, symbols, formatting -----------------------------------


@dataclass
class Hover:
    markdown: str
    span: Span


def _in(span: Span | None, line: int, ch: int) -> bool:
    return span is not None and span.line == line and span.start <= ch <= span.end


def hover(doc: Document, schema: Schema, line: int, character: int) -> Hover | None:
    for c in doc.walk():
        spec = schema.components.get(c.kind)
        if spec is None:
            continue
        if _in(c.kind_span, line, character):
            return Hover(f"**{c.kind}**\n\n{component_doc(spec)}", c.kind_span)
        for g in c.groups.values():
            gspec = spec.groups.get(g.name)
            if gspec and _in(g.name_span, line, character):
                return Hover(f"**{g.name}** (group)\n\nProperties: " + ", ".join(f"`{k}`" for k in gspec.properties),
                             g.name_span)
        for gname, info in [(None, c.info)] + [(g.name, g.info) for g in c.groups.values()]:
            for key, i in info.items():
                if not _in(i.key_span, line, character):
                    continue
                p = spec.property(key, gname)
                where = f"{c.kind} › {gname}" if gname else c.kind
                if p:
                    return Hover(f"**{key}** · {where}\n\n{property_doc(p)}", i.key_span)
                if gname is None and spec.pass_through and CAMEL_RE.match(key):
                    return Hover(f"**{key}**\n\nNot in the grammar; passed to ORDS as `{to_param(key)}`.", i.key_span)
    return None


def _values(c: Component):
    for v in list(c.props.values()) + [v for g in c.groups.values() for v in g.props.values()]:
        yield from (v.items if isinstance(v, Array) else (v,))


def definition(doc: Document, line: int, character: int) -> Component | None:
    """The component a @reference under the cursor points to."""
    targets = reference_targets(doc)
    for c in doc.walk():
        for v in _values(c):
            if isinstance(v, Reference) and _in(v.span, line, character):
                return targets.get(v.target)
    return None


def references(doc: Document, line: int, character: int) -> list[Span]:
    """Spans of every @reference to the component whose id is under the cursor."""
    target = next((c for c in doc.components if _in(c.id_span, line, character)), None)
    if target is None:
        ref = definition(doc, line, character)
        target = ref
    if target is None or target.id is None:
        return []
    return [v.span for c in doc.walk() for v in _values(c) if isinstance(v, Reference) and v.target == target.id]


def format_document(text: str) -> str | None:
    """Canonical layout, or None if formatting could lose something.

    Never reformats a document with errors, or one with comments after the
    header, which the document tree does not keep.
    """
    doc = apx.parse(text)
    if any(p.severity == "error" for p in doc.problems):
        return None
    in_fence = {n for f in doc.fences for n in range(f.start, f.end + 1)}
    body = doc.lines[len(doc.header):]
    if any(l.strip().startswith(("//", "/*")) for n, l in enumerate(body, len(doc.header)) if n not in in_fence):
        return None
    return apx.dumps(doc)
