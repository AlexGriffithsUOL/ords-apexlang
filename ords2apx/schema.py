"""The component schema, derived from the grammar.

Everything else (converter, language server, highlighting) reads the
components, nesting, properties, value types and documentation from here, so
grammar/ords-apexlang.ebnf is the single source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

from .ebnf import Alt, Expr, Grammar, Opt, Ref, Rule, Seq, Term, parse_ebnf, refs_of

GRAMMAR_FILE = "ords-apexlang.ebnf"

LAYOUT = {"indent", "ws", "required-ws", "line-end", "nl", "component-id", "blank-lines"}
PASS_THROUGH = "pass-through-property"

# value rule -> (type, code language)
VALUE_RULES = {
    "boolean": ("boolean", ""),
    "number": ("number", ""),
    "array-of-name": ("names", ""),
    "text": ("text", ""),
    "string-like-value": ("text", ""),
    "string": ("text", ""),
    "multiline-string": ("code", ""),
    "code": ("code", ""),
    "sql-code": ("code", "sql"),
    "plsql-code": ("code", "plsql"),
    "javascript-code": ("code", "javascript"),
}


class SchemaError(ValueError):
    pass


@dataclass
class PropertySpec:
    key: str
    type: str  # boolean | number | text | code | names | enum | value
    values: tuple[str, ...] = ()  # enum values; a quoted string is accepted too
    param: str | None = None  # ORDS parameter, e.g. p_base_path
    description: str = ""
    lang: str = ""  # code language for fenced values
    group: str | None = None
    default: str | None = None  # value ORDS uses when the property is left out, as written in .ords.apx
    generated: bool = False  # ORDS fills the value in when it is left out


@dataclass
class GroupSpec:
    name: str
    properties: dict[str, PropertySpec] = field(default_factory=dict)


@dataclass
class ComponentSpec:
    kind: str
    rule: str
    id: str  # required | optional | none
    procedure: str | None
    id_param: str | None
    description: str
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    groups: dict[str, GroupSpec] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    pass_through: bool = False
    parent: str | None = None

    def all_properties(self):
        """Direct properties, then group properties, in grammar order."""
        yield from self.properties.values()
        for g in self.groups.values():
            yield from g.properties.values()

    def property(self, key: str, group: str | None = None) -> PropertySpec | None:
        if group is None:
            return self.properties.get(key)
        g = self.groups.get(group)
        return g.properties.get(key) if g else None


@dataclass(eq=False)  # hashed by identity, so per-schema results can be cached
class Schema:
    components: dict[str, ComponentSpec]
    top_level: list[str]
    pass_through_doc: str = ""

    @property
    def enum_values(self) -> set[str]:
        return {v for c in self.components.values() for p in c.all_properties() for v in p.values}

    def parent_params(self, kind: str) -> tuple[str, ...]:
        """ORDS parameters naming a component's ancestors, outermost first."""
        spec = self.components[kind]
        if spec.parent is None:
            return ()
        parent = self.components[spec.parent]
        return self.parent_params(parent.kind) + ((parent.id_param,) if parent.id_param else ())


def _items(expr: Expr) -> tuple:
    return expr.items if isinstance(expr, Seq) else (expr,)


def _alternatives(expr: Expr) -> tuple:
    return expr.options if isinstance(expr, Alt) else (expr,)


def _first_term(expr: Expr) -> str | None:
    first = _items(expr)[0]
    return first.value if isinstance(first, Term) else None


def _has_term(expr: Expr, value: str) -> bool:
    return any(isinstance(e, Term) and e.value == value for e in _items(expr))


def _is_component_rule(rule: Rule) -> bool:
    kind = _first_term(rule.expr)
    return kind is not None and re.fullmatch(r"[A-Za-z]+", kind) is not None and _has_term(rule.expr, "(")


def _is_group_rule(rule: Rule) -> bool:
    seq = [e for e in _items(rule.expr) if not (isinstance(e, Ref) and e.name in LAYOUT)]
    return bool(seq) and isinstance(seq[0], Term) and _has_term(rule.expr, "{")


def _value_type(expr: Expr) -> tuple[str, tuple[str, ...], str]:
    if isinstance(expr, Ref):
        type_, lang = VALUE_RULES.get(expr.name, ("value", ""))
        return type_, (), lang
    if isinstance(expr, Alt):
        values = tuple(o.value for o in expr.options if isinstance(o, Term))
        if values:
            return "enum", values, ""
    return "value", (), ""


def _parse_doc(doc: str | None) -> tuple[str | None, str, str | None, bool]:
    """'p_param [default X]: description' -> (param, description, default, generated)."""
    if not doc:
        return None, "", None, False
    m = re.fullmatch(r"(-|p_\w+)(?:\s*\[(default\s+([^\]]+)|generated)\])?:\s*(.*)", doc, re.S)
    if not m:
        return None, doc, None, False
    param = None if m.group(1) == "-" else m.group(1)
    return param, " ".join(m.group(4).split()), m.group(3), m.group(2) == "generated"


class _Builder:
    def __init__(self, grammar: Grammar):
        self.g = grammar

    def rule(self, name: str) -> Rule:
        if name not in self.g.rules:
            raise SchemaError(f"<{name}> is referenced but not defined")
        return self.g.rules[name]

    def properties_of(self, rule: Rule, into: dict, owner: ComponentSpec | None, group: str | None) -> None:
        for alt in _alternatives(rule.expr):
            if isinstance(alt, Ref) and alt.name == PASS_THROUGH:
                if owner is None:
                    raise SchemaError(f"<{rule.name}>: groups cannot pass properties through")
                owner.pass_through = True
                continue
            seq = _items(alt)
            if not (len(seq) >= 3 and isinstance(seq[0], Term) and isinstance(seq[1], Term) and seq[1].value == ":"):
                raise SchemaError(f'<{rule.name}> line {rule.line}: expected "key" ":" <ws> value')
            key = seq[0].value
            value = [e for e in seq[2:] if not (isinstance(e, Ref) and e.name in LAYOUT)]
            if len(value) != 1:
                raise SchemaError(f"<{rule.name}> line {rule.line}: property '{key}' needs one value")
            type_, values, lang = _value_type(value[0])
            param, description, default, generated = _parse_doc(alt.doc if isinstance(alt, Seq) else None)
            if key in into:
                raise SchemaError(f"<{rule.name}> line {rule.line}: property '{key}' is defined twice")
            into[key] = PropertySpec(key, type_, values, param, description, lang, group, default, generated)

    def collect_body(self, expr: Expr, spec: ComponentSpec, seen: set[str]) -> None:
        for name in refs_of(expr):
            if name in LAYOUT or name in seen or name in self.g.primitives:
                continue
            seen.add(name)
            rule = self.rule(name)
            if _is_component_rule(rule):
                spec.children.append(_first_term(rule.expr))
            elif _is_group_rule(rule):
                gname = next(e.value for e in _items(rule.expr) if isinstance(e, Term))
                group = GroupSpec(gname)
                for ref in refs_of(rule.expr):
                    if ref not in LAYOUT:
                        self.properties_of(self.rule(ref), group.properties, None, gname)
                spec.groups[gname] = group
            elif name.endswith("-property"):
                self.properties_of(rule, spec.properties, spec, None)
            elif name == PASS_THROUGH:
                spec.pass_through = True
            else:
                self.collect_body(rule.expr, spec, seen)

    def component(self, rule: Rule) -> ComponentSpec:
        seq = _items(rule.expr)
        kind = seq[0].value
        if len(seq) > 1 and isinstance(seq[1], Opt) and "component-id" in refs_of(seq[1]):
            id_ = "optional"
        elif any(isinstance(e, Ref) and e.name == "component-id" for e in seq):
            id_ = "required"
        else:
            id_ = "none"
        # documentation: "kind\nORDS.PROC; id is p_param\ndescription"
        lines = (rule.doc or "").split("\n")
        header = re.match(r"\s*([\w.-]+);\s*id is\s*([\w-]+)", lines[1]) if len(lines) > 1 else None
        body = lines[2 if header else 1:] if lines[0].strip() == kind else lines
        spec = ComponentSpec(
            kind=kind,
            rule=rule.name,
            id=id_,
            procedure=header.group(1) if header and header.group(1) != "-" else None,
            id_param=header.group(2) if header and header.group(2) != "-" else None,
            description=" ".join(" ".join(body).split()),
        )
        start = next(i for i, e in enumerate(seq) if isinstance(e, Term) and e.value == "(")
        self.collect_body(Seq(seq[start:]), spec, set())
        return spec

    def build(self) -> Schema:
        components: dict[str, ComponentSpec] = {}
        for rule in self.g.rules.values():
            if _is_component_rule(rule):
                spec = self.component(rule)
                if spec.kind in components:
                    raise SchemaError(f"component kind '{spec.kind}' is defined twice")
                components[spec.kind] = spec
        for spec in components.values():
            for child in spec.children:
                components[child].parent = spec.kind
        if "component" not in self.g.rules:
            raise SchemaError("the grammar has no <component> index rule")
        top = [_first_term(self.rule(n).expr) for n in refs_of(self.g.rules["component"].expr)]
        pt = self.g.rules.get(PASS_THROUGH)
        return Schema(components, top, " ".join((pt.doc or "").split()) if pt else "")


def build_schema(grammar: Grammar) -> Schema:
    return _Builder(grammar).build()


def bundled_grammar_text() -> str:
    return resources.files(__package__).joinpath("grammar", GRAMMAR_FILE).read_text(encoding="utf-8")


def load_schema(path: str | Path | None = None) -> Schema:
    text = Path(path).read_text(encoding="utf-8") if path else bundled_grammar_text()
    return build_schema(parse_ebnf(text))


@cache
def default_schema() -> Schema:
    return load_schema()
