"""Parser for the EBNF dialect of Oracle's apexlang.ebnf and ords-apexlang.ebnf.

    <rule> ::= "term" <other-rule> [ optional ] { repeated } ( a | b ) "A".."Z"
    (* comment *)

A comment on its own line(s) before a rule documents that rule. A comment at
the end of an alternative, on the same line, documents that alternative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Union


@dataclass(frozen=True)
class Term:
    value: str


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Range:
    start: str
    end: str


@dataclass(frozen=True)
class Seq:
    items: tuple
    doc: str | None = None


@dataclass(frozen=True)
class Alt:
    options: tuple


@dataclass(frozen=True)
class Opt:
    expr: "Expr"


@dataclass(frozen=True)
class Rep:
    expr: "Expr"


Expr = Union[Term, Ref, Range, Seq, Alt, Opt, Rep]


@dataclass
class Rule:
    name: str
    expr: Expr
    doc: str | None
    line: int


@dataclass
class Grammar:
    rules: dict[str, Rule] = field(default_factory=dict)
    # rules referenced but never defined: character classes like <any-character>
    primitives: set[str] = field(default_factory=set)


class EbnfError(ValueError):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass
class _Tok:
    type: str  # ref | term | define | | [ ] { } ( ) .. | comment
    value: str
    line: int
    end_line: int


ESCAPES = {"n": "\n", "r": "\r", "t": "\t"}


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, line = 0, 1
    while i < len(text):
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif text.startswith("(*", i):
            end = text.find("*)", i + 2)
            if end < 0:
                raise EbnfError("unterminated comment", line)
            body = text[i + 2:end]
            start = line
            line += body.count("\n")
            toks.append(_Tok("comment", body, start, line))
            i = end + 2
        elif c == "<":
            end = text.find(">", i)
            if end < 0:
                raise EbnfError("unterminated <rule-name>", line)
            toks.append(_Tok("ref", text[i + 1:end], line, line))
            i = end + 1
        elif c == '"':
            value = []
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == "\\" and i + 1 < len(text):
                    value.append(ESCAPES.get(text[i + 1], text[i + 1]))
                    i += 2
                else:
                    if text[i] == "\n":
                        raise EbnfError("unterminated string", line)
                    value.append(text[i])
                    i += 1
            if i >= len(text):
                raise EbnfError("unterminated string", line)
            i += 1
            toks.append(_Tok("term", "".join(value), line, line))
        elif text.startswith("::=", i):
            toks.append(_Tok("define", "::=", line, line))
            i += 3
        elif text.startswith("..", i):
            toks.append(_Tok("..", "..", line, line))
            i += 2
        elif c in "|[]{}()":
            toks.append(_Tok(c, c, line, line))
            i += 1
        else:
            raise EbnfError(f"unexpected character {c!r}", line)
    return toks


def _clean_comment(body: str) -> str:
    """Drop the common indentation of continuation lines, keep line structure."""
    lines = [l.rstrip() for l in body.split("\n")]
    rest = [l for l in lines[1:] if l.strip()]
    indent = min((len(l) - len(l.lstrip()) for l in rest), default=0)
    return "\n".join([lines[0].strip()] + [l[indent:] for l in lines[1:]]).strip()


class _Parser:
    def __init__(self, toks: list[_Tok]):
        self.toks = toks
        self.pos = 0

    def peek(self, offset: int = 0) -> _Tok | None:
        i = self.pos + offset
        return self.toks[i] if i < len(self.toks) else None

    def at_rule_start(self) -> bool:
        a, b = self.peek(), self.peek(1)
        return a is not None and b is not None and a.type == "ref" and b.type == "define"

    def expect(self, type_: str) -> _Tok:
        t = self.peek()
        if t is None or t.type != type_:
            raise EbnfError(f"expected {type_!r} but found {t.value if t else 'end of file'!r}", t.line if t else 0)
        self.pos += 1
        return t

    def parse(self) -> Grammar:
        g = Grammar()
        doc = None
        while (t := self.peek()) is not None:
            if t.type == "comment":
                doc = _clean_comment(t.value)
                self.pos += 1
                continue
            if not self.at_rule_start():
                raise EbnfError(f"expected a rule definition, found {t.value!r}", t.line)
            name = self.expect("ref").value
            self.expect("define")
            if name in g.rules:
                raise EbnfError(f"rule <{name}> is defined twice", t.line)
            g.rules[name] = Rule(name, self.alternation(True), doc, t.line)
            doc = None
        for rule in g.rules.values():
            g.primitives.update(r for r in refs_of(rule.expr) if r not in g.rules)
        return g

    def alternation(self, top: bool) -> Expr:
        options = [self.sequence(top)]
        while (t := self.peek()) is not None and t.type == "|":
            self.pos += 1
            options.append(self.sequence(top))
        return options[0] if len(options) == 1 else Alt(tuple(options))

    def sequence(self, top: bool) -> Expr:
        items: list[Expr] = []
        doc = None
        last_line = self.peek().line if self.peek() else 0
        while (t := self.peek()) is not None and not (top and self.at_rule_start()):
            if t.type == "comment":
                # a trailing comment documents this alternative; one on a
                # later line belongs to the next rule
                if top and t.line > last_line:
                    break
                doc = _clean_comment(t.value)
                self.pos += 1
                continue
            item = self.primary()
            if item is None:
                break
            items.append(item)
            last_line = self.toks[self.pos - 1].end_line
        if not items:
            raise EbnfError("empty alternative", self.peek().line if self.peek() else last_line)
        return items[0] if len(items) == 1 and doc is None else Seq(tuple(items), doc)

    def primary(self) -> Expr | None:
        t = self.peek()
        if t.type == "ref":
            self.pos += 1
            return Ref(t.value)
        if t.type == "term":
            self.pos += 1
            if (n := self.peek()) is not None and n.type == "..":
                self.pos += 1
                return Range(t.value, self.expect("term").value)
            return Term(t.value)
        if t.type in "[{(":
            self.pos += 1
            inner = self.alternation(False)
            self.expect({"[": "]", "{": "}", "(": ")"}[t.type])
            return Opt(inner) if t.type == "[" else Rep(inner) if t.type == "{" else inner
        return None


def refs_of(expr: Expr) -> Iterator[str]:
    match expr:
        case Ref(name):
            yield name
        case Seq(items):
            for e in items:
                yield from refs_of(e)
        case Alt(options):
            for e in options:
                yield from refs_of(e)
        case Opt(e) | Rep(e):
            yield from refs_of(e)


def parse_ebnf(text: str) -> Grammar:
    return _Parser(_tokenize(text)).parse()
