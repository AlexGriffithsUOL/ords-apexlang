"""Markdown reference of every component and property, generated from the grammar.

    ords2apx reference > skills/ords-apexlang/ords-apexlang-generation/references/components.md
"""

from __future__ import annotations

from . import __version__
from .schema import ComponentSpec, PropertySpec, Schema

TYPES = {"boolean": "`true` \\| `false`", "number": "number", "text": "text", "code": "code (fenced)",
         "names": "array of names / `@references`", "value": "any value"}


def _value(p: PropertySpec) -> str:
    if p.type == "enum":
        return " \\| ".join(f"`{v}`" for v in p.values) + " \\| `\"quoted ORDS value\"`"
    if p.type == "code" and p.lang:
        return f"{p.lang} code (fenced)"
    return TYPES[p.type]


def _row(p: PropertySpec) -> str:
    notes = p.description.replace("|", "\\|")
    if p.default is not None:
        notes += f" Default `{p.default}`."
    elif p.generated:
        notes += " Filled in by ORDS when left out."
    return f"| `{p.key}` | {_value(p)} | {f'`{p.param}`' if p.param else '—'} | {notes} |"


def _component(c: ComponentSpec, schema: Schema, out: list[str]) -> None:
    out.append(f"## {c.kind}")
    out.append("")
    out.append(c.description)
    out.append("")
    facts = []
    if c.procedure:
        facts.append(f"ORDS procedure: `{c.procedure}`")
    facts.append({"required": f"id: required (`{c.id_param}`)" if c.id_param else "id: required",
                  "optional": f"id: optional (`{c.id_param}`)", "none": "id: none"}[c.id])
    facts.append(f"nested in: `{c.parent}`" if c.parent else "top level")
    if c.children:
        facts.append("contains: " + ", ".join(f"`{k}`" for k in c.children))
    out += [f"- {f}" for f in facts]
    out.append("")
    header = ["| Property | Value | ORDS parameter | Notes |", "| --- | --- | --- | --- |"]
    if c.properties:
        out += header + [_row(p) for p in c.properties.values()]
        out.append("")
    for g in c.groups.values():
        out.append(f"Group `{g.name} {{ … }}`:")
        out.append("")
        out += header + [_row(p) for p in g.properties.values()]
        out.append("")
    if c.pass_through:
        out.append("Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), "
                   "with a warning.")
        out.append("")


def generate(schema: Schema) -> str:
    out = [
        "# ORDS APEXlang component reference",
        "",
        f"Generated from the grammar by `ords2apx reference` (ords2apx {__version__}). Do not edit by hand.",
        "",
        "Components in nesting order:",
        "",
    ]

    def tree(kind: str, depth: int) -> None:
        out.append(f"{'  ' * depth}- `{kind}`")
        for child in schema.components[kind].children:
            tree(child, depth + 1)

    for kind in schema.top_level:
        tree(kind, 0)
    out.append("")
    seen: set[str] = set()

    def visit(kind: str) -> None:
        if kind in seen:
            return
        seen.add(kind)
        _component(schema.components[kind], schema, out)
        for child in schema.components[kind].children:
            visit(child)

    for kind in schema.top_level:
        visit(kind)
    return "\n".join(out).rstrip() + "\n"
