"""Generic APEXlang document model, reader and writer.

Follows the "Core layout and values" section of Oracle's apexlang.ebnf
(APEX 26.1) and the formatting of Oracle's published example applications:

    kind id (                  component, body in parentheses
        key: value             property, value runs to end of line
        group {                property group
            key: value
        }
        key:
            ```sql             multi-line string, fenced, optional language tag
            select 1 from dual
            ```

        child id (             child components, separated by blank lines
        )

    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

INDENT = "    "
FENCE = "```"

IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*\Z")
NUMBER_RE = re.compile(r"-?\d+(\.\d+)?\Z")
COMPONENT_RE = re.compile(r'([A-Za-z][A-Za-z0-9]*)(?:\s+("(?:[^"\\]|\\.)*"|[A-Za-z0-9_][A-Za-z0-9_.\-]*))?\s*\(\Z')
GROUP_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\s*\{\Z")
PROPERTY_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*):(?:\s+(.*))?\Z")


class ApxSyntaxError(ValueError):
    def __init__(self, message: str, line_no: int):
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


# --- values -----------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """A string-like value. `quoted` records whether it was written in quotes."""

    value: str
    quoted: bool = False


@dataclass(frozen=True)
class Number:
    text: str


@dataclass(frozen=True)
class Boolean:
    value: bool


@dataclass(frozen=True)
class Reference:
    target: str


@dataclass(frozen=True)
class Code:
    text: str
    lang: str = ""


@dataclass(frozen=True)
class Array:
    items: tuple


Value = Text | Number | Boolean | Reference | Code | Array


# --- document tree ----------------------------------------------------------


@dataclass
class Group:
    name: str
    props: dict[str, Value] = field(default_factory=dict)


@dataclass
class Component:
    kind: str
    id: str | None = None
    props: dict[str, Value] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    children: list[Component] = field(default_factory=list)
    line_no: int = 0

    def get(self, key: str, group: str | None = None) -> Value | None:
        if group is None:
            return self.props.get(key)
        g = self.groups.get(group)
        return g.props.get(key) if g else None

    def set(self, key: str, value: Value | None, group: str | None = None) -> None:
        if value is None:
            return
        if group is None:
            self.props[key] = value
        else:
            self.groups.setdefault(group, Group(group)).props[key] = value


@dataclass
class Document:
    components: list[Component] = field(default_factory=list)
    header: list[str] = field(default_factory=list)


# --- writer -----------------------------------------------------------------


def _needs_quotes(s: str) -> bool:
    return (
        s == ""
        or s != s.strip()
        or s in ("true", "false")
        or NUMBER_RE.match(s) is not None
        or s[0] in '"@['
        or s.startswith(FENCE)
        or s.startswith("//")
        or s.startswith("/*")
        or any(c in s for c in "\n\r")
    )


def _scalar(value: Value) -> str:
    match value:
        case Text(s, quoted):
            return json.dumps(s, ensure_ascii=False) if quoted or _needs_quotes(s) else s
        case Number(text):
            return text
        case Boolean(b):
            return "true" if b else "false"
        case Reference(target):
            return "@" + target if IDENTIFIER_RE.match(target) else "@" + json.dumps(target)
    raise TypeError(f"not a scalar: {value!r}")


def _array_item(value: Value) -> str:
    if isinstance(value, Text) and (not IDENTIFIER_RE.match(value.value) or _needs_quotes(value.value)):
        return json.dumps(value.value, ensure_ascii=False)
    return _scalar(value)


def _write_props(out: list[str], props: dict[str, Value], depth: int) -> None:
    pad = INDENT * depth
    for key, value in props.items():
        if isinstance(value, Code):
            if FENCE in value.text:
                # cannot be fenced; fall back to an escaped single-line string
                out.append(f"{pad}{key}: {json.dumps(value.text, ensure_ascii=False)}")
                continue
            body = pad + INDENT
            out.append(f"{pad}{key}:")
            out.append(f"{body}{FENCE}{value.lang}")
            for line in value.text.split("\n"):
                out.append(body + line if line else "")
            out.append(f"{body}{FENCE}")
        elif isinstance(value, Array):
            out.append(f"{pad}{key}: [{' '.join(_array_item(v) for v in value.items)}]")
        else:
            out.append(f"{pad}{key}: {_scalar(value)}")


def _write_component(out: list[str], c: Component, depth: int) -> None:
    pad = INDENT * depth
    head = c.kind
    if c.id is not None:
        head += " " + (c.id if IDENTIFIER_RE.match(c.id) else json.dumps(c.id, ensure_ascii=False))
    out.append(f"{pad}{head} (")
    _write_props(out, c.props, depth + 1)
    for g in c.groups.values():
        if not g.props:
            continue
        out.append(f"{pad}{INDENT}{g.name} {{")
        _write_props(out, g.props, depth + 2)
        out.append(f"{pad}{INDENT}}}")
    for child in c.children:
        out.append("")
        _write_component(out, child, depth + 1)
    if c.children:
        out.append("")
    out.append(f"{pad})")


def dumps(doc: Document) -> str:
    out: list[str] = [f"// {line}" if line else "//" for line in doc.header]
    if out:
        out.append("")
    for i, c in enumerate(doc.components):
        if i:
            out.append("")
        _write_component(out, c, 0)
    return "\n".join(out) + "\n"


# --- reader -----------------------------------------------------------------


def _parse_scalar(raw: str, line_no: int) -> Value:
    if raw.startswith('"'):
        try:
            return Text(json.loads(raw), quoted=True)
        except json.JSONDecodeError as e:
            raise ApxSyntaxError(f"bad string literal {raw}: {e.msg}", line_no) from None
    if raw.startswith("@"):
        target = raw[1:]
        if target.startswith('"'):
            target = json.loads(target)
        return Reference(target)
    if raw in ("true", "false"):
        return Boolean(raw == "true")
    if NUMBER_RE.match(raw):
        return Number(raw)
    return Text(raw)


ARRAY_TOKEN_RE = re.compile(r'\s*("(?:[^"\\]|\\.)*"|@"(?:[^"\\]|\\.)*"|[^\s\]]+)')


def _parse_array(raw: str, line_no: int) -> Array:
    inner = raw[1:-1].strip()
    items = []
    pos = 0
    while pos < len(inner):
        m = ARRAY_TOKEN_RE.match(inner, pos)
        if not m:
            break
        items.append(_parse_scalar(m.group(1), line_no))
        pos = m.end()
        while pos < len(inner) and inner[pos].isspace():
            pos += 1
    return Array(tuple(items))


class _Reader:
    def __init__(self, text: str):
        self.lines = text.replace("\r\n", "\n").split("\n")
        self.i = 0

    def _next_significant(self) -> tuple[int, str] | None:
        """Advance past blank lines and comments; return (line_no, stripped)."""
        while self.i < len(self.lines):
            stripped = self.lines[self.i].strip()
            if not stripped or stripped.startswith("//"):
                self.i += 1
                continue
            if stripped.startswith("/*"):
                start = self.i
                while "*/" not in self.lines[self.i]:
                    self.i += 1
                    if self.i >= len(self.lines):
                        raise ApxSyntaxError("unterminated /* comment", start + 1)
                self.i += 1
                continue
            return self.i + 1, stripped
        return None

    def _read_fence(self, fence_line: str, line_no: int) -> Code:
        lang = fence_line.strip()[len(FENCE):].strip()
        indent = fence_line[: len(fence_line) - len(fence_line.lstrip())]
        body: list[str] = []
        while True:
            if self.i >= len(self.lines):
                raise ApxSyntaxError("unterminated ``` block", line_no)
            line = self.lines[self.i]
            self.i += 1
            if line.strip() == FENCE:
                return Code("\n".join(body), lang)
            if line.startswith(indent):
                body.append(line[len(indent):])
            elif not line.strip():
                body.append("")
            else:
                body.append(line.lstrip())

    def _read_property(self, key: str, rest: str | None, line_no: int) -> Value:
        if not rest:
            # value is a fenced block on the following line(s)
            nxt = self._next_significant()
            if nxt is None or not nxt[1].startswith(FENCE):
                raise ApxSyntaxError(f"property '{key}' has no value", line_no)
            fence_line = self.lines[self.i]
            self.i += 1
            return self._read_fence(fence_line, nxt[0])
        rest = rest.rstrip()
        if rest.startswith(FENCE):
            line = self.lines[self.i - 1]
            return self._read_fence(line[: len(line) - len(line.lstrip())] + rest, line_no)
        if rest.startswith("["):
            while not rest.endswith("]"):
                if self.i >= len(self.lines):
                    raise ApxSyntaxError("unterminated array", line_no)
                rest += " " + self.lines[self.i].strip()
                self.i += 1
            return _parse_array(rest, line_no)
        return _parse_scalar(rest, line_no)

    def _read_body(self, close: str, line_no: int, props: dict, groups=None, children=None) -> None:
        while True:
            nxt = self._next_significant()
            if nxt is None:
                raise ApxSyntaxError(f"missing closing '{close}'", line_no)
            n, s = nxt
            self.i += 1
            if s == close:
                return
            if m := PROPERTY_RE.match(s):
                key = m.group(1)
                if key in props:
                    raise ApxSyntaxError(f"duplicate property '{key}'", n)
                props[key] = self._read_property(key, m.group(2), n)
            elif groups is not None and (m := GROUP_RE.match(s)):
                g = groups.setdefault(m.group(1), Group(m.group(1)))
                self._read_body("}", n, g.props)
            elif children is not None and (m := COMPONENT_RE.match(s)):
                children.append(self._read_component(m, n))
            else:
                raise ApxSyntaxError(f"unexpected: {s}", n)

    def _read_component(self, m: re.Match, line_no: int) -> Component:
        cid = m.group(2)
        if cid is not None and cid.startswith('"'):
            cid = json.loads(cid)
        c = Component(m.group(1), cid, line_no=line_no)
        self._read_body(")", line_no, c.props, c.groups, c.children)
        return c

    def read(self) -> Document:
        doc = Document()
        # leading // lines form the header
        while self.i < len(self.lines) and self.lines[self.i].startswith("//"):
            doc.header.append(self.lines[self.i][2:].removeprefix(" "))
            self.i += 1
        while (nxt := self._next_significant()) is not None:
            n, s = nxt
            self.i += 1
            m = COMPONENT_RE.match(s)
            if not m:
                raise ApxSyntaxError(f"expected a component declaration, got: {s}", n)
            doc.components.append(self._read_component(m, n))
        return doc


def loads(text: str) -> Document:
    return _Reader(text).read()
