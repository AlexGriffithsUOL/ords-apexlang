"""APEXlang document model, reader and writer.

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

The reader is fault tolerant: it records positions for the language server
and collects problems instead of stopping at the first one. `loads` is the
strict form the converter uses.
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
ARRAY_TOKEN_RE = re.compile(r'\s*("(?:[^"\\]|\\.)*"|@"(?:[^"\\]|\\.)*"|[^\s\]]+|\])')


@dataclass(frozen=True)
class Span:
    line: int  # 0-based
    start: int
    end: int


class ApxSyntaxError(ValueError):
    def __init__(self, message: str, line_no: int):
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


@dataclass(frozen=True)
class Problem:
    message: str
    span: Span
    severity: str = "error"  # error | warning


# --- values -----------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """A string-like value. `quoted` records whether it was written in quotes."""

    value: str
    quoted: bool = False
    span: Span | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Number:
    text: str
    span: Span | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Boolean:
    value: bool
    span: Span | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Reference:
    target: str
    span: Span | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Code:
    text: str
    lang: str = ""
    span: Span | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Array:
    items: tuple
    span: Span | None = field(default=None, compare=False, repr=False)


Value = Text | Number | Boolean | Reference | Code | Array


# --- document tree ----------------------------------------------------------


@dataclass
class PropertyInfo:
    key_span: Span
    end_line: int  # last line the property occupies


@dataclass
class Group:
    name: str
    props: dict[str, Value] = field(default_factory=dict)
    name_span: Span | None = field(default=None, compare=False, repr=False)
    start_line: int = field(default=0, compare=False, repr=False)
    end_line: int = field(default=0, compare=False, repr=False)
    info: dict[str, PropertyInfo] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Component:
    kind: str
    id: str | None = None
    props: dict[str, Value] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    children: list[Component] = field(default_factory=list)
    line_no: int = field(default=0, compare=False)  # 1-based, for messages
    kind_span: Span | None = field(default=None, compare=False, repr=False)
    id_span: Span | None = field(default=None, compare=False, repr=False)
    end_line: int = field(default=0, compare=False, repr=False)
    info: dict[str, PropertyInfo] = field(default_factory=dict, compare=False, repr=False)
    parent: Component | None = field(default=None, compare=False, repr=False)

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

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Fence:
    start: int  # line of the opening fence
    end: int  # line of the closing fence
    lang: str


@dataclass
class Document:
    components: list[Component] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    fences: list[Fence] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def walk(self):
        for c in self.components:
            yield from c.walk()


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


def format_id(cid: str) -> str:
    return cid if IDENTIFIER_RE.match(cid) else json.dumps(cid, ensure_ascii=False)


def _write_component(out: list[str], c: Component, depth: int) -> None:
    pad = INDENT * depth
    head = c.kind if c.id is None else f"{c.kind} {format_id(c.id)}"
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


class _Reader:
    def __init__(self, text: str):
        self.doc = Document(lines=text.replace("\r\n", "\n").split("\n"))
        self.lines = self.doc.lines
        self.i = 0

    def problem(self, message: str, span: Span, severity: str = "error") -> None:
        self.doc.problems.append(Problem(message, span, severity))

    def line_span(self, line: int) -> Span:
        text = self.lines[line] if line < len(self.lines) else ""
        start = len(text) - len(text.lstrip())
        return Span(line, start, max(start + 1, len(text.rstrip())))

    def next_significant(self) -> int | None:
        """Skip blank lines and comments; return the next significant line."""
        while self.i < len(self.lines):
            s = self.lines[self.i].strip()
            if not s or s.startswith("//"):
                self.i += 1
                continue
            if s.startswith("/*"):
                start = self.i
                while self.i < len(self.lines) and "*/" not in self.lines[self.i]:
                    self.i += 1
                if self.i >= len(self.lines):
                    self.problem("unterminated /* comment", self.line_span(start))
                    return None
                self.i += 1
                continue
            return self.i
        return None

    def read_fence(self, fence_line: int) -> Code:
        raw = self.lines[fence_line]
        indent = len(raw) - len(raw.lstrip())
        lang = raw.strip()[len(FENCE):].strip()
        body: list[str] = []
        line = fence_line + 1
        while line < len(self.lines) and self.lines[line].strip() != FENCE:
            l = self.lines[line]
            body.append(l.lstrip() if l[:indent].strip() else l[indent:])
            line += 1
        if line >= len(self.lines):
            self.problem("unterminated ``` block", self.line_span(fence_line))
            line = len(self.lines) - 1
        self.doc.fences.append(Fence(fence_line, line, lang))
        self.i = line + 1
        return Code("\n".join(body), lang, Span(fence_line, indent, len(raw.rstrip())))

    def scalar(self, raw: str, span: Span) -> Value:
        if raw.startswith('"'):
            try:
                value = json.loads(raw)
                if not isinstance(value, str):
                    raise ValueError
            except ValueError:
                self.problem(f"malformed string literal {raw}", span)
                value = raw
            return Text(value, True, span)
        if raw.startswith("@"):
            target = raw[1:]
            if target.startswith('"'):
                try:
                    target = json.loads(target)
                except ValueError:
                    self.problem(f"malformed reference {raw}", span)
            return Reference(target, span)
        if raw in ("true", "false"):
            return Boolean(raw == "true", span)
        if NUMBER_RE.match(raw):
            return Number(raw, span)
        return Text(raw, False, span)

    def array(self, line: int, start_col: int) -> Array:
        items: list[Value] = []
        l, col = line, start_col + 1
        while True:
            text = self.lines[l]
            pos = col
            while (m := ARRAY_TOKEN_RE.match(text, pos)) and m.end() > pos:
                tok = m.group(1)
                start = m.end() - len(tok)
                pos = m.end()
                if tok == "]":
                    self.i = l + 1
                    return Array(tuple(items), Span(line, start_col, start + 1))
                items.append(self.scalar(tok, Span(l, start, start + len(tok))))
            l, col = l + 1, 0
            if l >= len(self.lines) or PROPERTY_RE.match(self.lines[l].strip()) or self.lines[l].strip() in (")", "}"):
                self.problem("unterminated array: missing ]", Span(line, start_col, start_col + 1))
                self.i = l
                return Array(tuple(items), Span(line, start_col, start_col + 1))

    def property(self, line: int, m: re.Match) -> tuple[str, Value | None, PropertyInfo]:
        raw = self.lines[line]
        key = m.group(1)
        key_start = raw.index(key)
        info = PropertyInfo(Span(line, key_start, key_start + len(key)), line)
        self.i = line + 1
        rest = (m.group(2) or "").rstrip()
        value_start = len(raw) - len(raw.lstrip()) + m.start(2) if m.group(2) else len(raw)
        value: Value | None
        if not rest:
            # value is a fenced block on the following line
            nxt = self.next_significant()
            if nxt is not None and self.lines[nxt].strip().startswith(FENCE):
                value = self.read_fence(nxt)
            else:
                self.i = line + 1
                self.problem(f"property '{key}' has no value", info.key_span)
                value = None
        elif rest.startswith(FENCE):
            value = self.read_fence(line)
        elif rest.startswith("["):
            value = self.array(line, value_start)
        else:
            value = self.scalar(rest, Span(line, value_start, value_start + len(rest)))
        info.end_line = self.i - 1
        return key, value, info

    def body(self, close: str, start_line: int, target: Component | Group, depth: int) -> int:
        """Read a body up to its closing delimiter; return the closing line."""
        component = target if isinstance(target, Component) else None
        while True:
            n = self.next_significant()
            if n is None:
                self.problem(f"missing closing '{close}'", self.line_span(start_line))
                return len(self.lines) - 1
            s = self.lines[n].strip()
            if s == close:
                self.i = n + 1
                return n
            if m := PROPERTY_RE.match(s):
                key, value, info = self.property(n, m)
                if key in target.info:
                    self.problem(f"duplicate property '{key}'", info.key_span)
                elif value is not None:
                    target.props[key] = value
                    target.info[key] = info
            elif component is not None and (m := GROUP_RE.match(s)):
                raw = self.lines[n]
                start = raw.index(m.group(1))
                group = Group(m.group(1), name_span=Span(n, start, start + len(m.group(1))), start_line=n)
                self.i = n + 1
                group.end_line = self.body("}", n, group, depth + 1)
                if group.name in component.groups:
                    self.problem(f"duplicate group '{group.name}'", group.name_span)
                else:
                    component.groups[group.name] = group
            elif component is not None and (m := COMPONENT_RE.match(s)):
                child = self.component(n, m, depth + 1)
                child.parent = component
                component.children.append(child)
            elif s in (")", "}") and depth > 0:
                # closes an outer block: report it and let the caller consume it
                self.problem(f"expected '{close}' before '{s}'", self.line_span(n))
                return n - 1
            else:
                self.problem(self.explain(s), self.line_span(n))
                self.i = n + 1

    @staticmethod
    def explain(s: str) -> str:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", s):
            return f"'{s}' needs a value ('{s}: …') or a body ('{s} (' / '{s} {{')"
        if re.fullmatch(r"[A-Za-z]\w*\s+.*\(", s):
            return 'component ids must be identifiers or "quoted strings"'
        if re.match(r"[A-Za-z][A-Za-z0-9]*\s*:", s):
            return "expected a space after ':'"
        return f"unexpected: {s[:40] + '…' if len(s) > 40 else s}"

    def component(self, line: int, m: re.Match, depth: int) -> Component:
        raw = self.lines[line]
        kind_start = raw.index(m.group(1))
        c = Component(m.group(1), line_no=line + 1, kind_span=Span(line, kind_start, kind_start + len(m.group(1))))
        if m.group(2) is not None:
            id_start = raw.index(m.group(2), kind_start + len(m.group(1)))
            cid = m.group(2)
            c.id = json.loads(cid) if cid.startswith('"') else cid
            c.id_span = Span(line, id_start, id_start + len(cid))
        self.i = line + 1
        c.end_line = self.body(")", line, c, depth)
        return c

    def read(self) -> Document:
        # leading // lines form the header
        while self.i < len(self.lines) and self.lines[self.i].startswith("//"):
            self.doc.header.append(self.lines[self.i][2:].removeprefix(" "))
            self.i += 1
        while (n := self.next_significant()) is not None:
            s = self.lines[n].strip()
            if m := COMPONENT_RE.match(s):
                self.doc.components.append(self.component(n, m, 0))
            else:
                self.problem(f"unmatched '{s}'" if s in (")", "}") else "expected a component, e.g. restModule name (",
                             self.line_span(n))
                self.i = n + 1
        return self.doc


def parse(text: str) -> Document:
    """Parse tolerantly: problems are collected in `Document.problems`."""
    return _Reader(text).read()


def loads(text: str) -> Document:
    """Parse strictly: raise on the first error."""
    doc = parse(text)
    errors = [p for p in doc.problems if p.severity == "error"]
    if errors:
        first = min(errors, key=lambda p: (p.span.line, p.span.start))
        raise ApxSyntaxError(first.message, first.span.line + 1)
    return doc
