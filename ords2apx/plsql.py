"""Read the statements out of an ORDS export script.

An ORDS export (SQL Developer, SQLcl `rest export`, ORDS_EXPORT) is a SQL*Plus
script holding one or more anonymous PL/SQL blocks. This module does not try
to understand PL/SQL in general. It extracts the simple statement forms
exports use and keeps everything else as raw text:

    PKG.PROC(p_a => 'x', p_b => 25, p_c => TRUE);   -> Call
    l_roles(1) := 'hr_role';                        -> array bookkeeping
    l_roles.DELETE;                                 -> array bookkeeping
    COMMIT;                                         -> dropped
    anything else                                   -> Raw
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ORDS package constants that hand-written scripts use instead of literals.
ORDS_CONSTANTS = {
    "SOURCE_TYPE_COLLECTION_FEED": "json/collection",
    "SOURCE_TYPE_COLLECTION_ITEM": "json/item",
    "SOURCE_TYPE_CSV_QUERY": "csv/query",
    "SOURCE_TYPE_MEDIA": "resource/lob",
    "SOURCE_TYPE_PLSQL": "plsql/block",
    "SOURCE_TYPE_QUERY": "json/query",
    "SOURCE_TYPE_QUERY_ONE_ROW": "json/query;type=single_row",
}

SQLPLUS_COMMAND_RE = re.compile(
    r"\s*(set|prompt|pro|rem|remark|whenever|spool|define|undefine|clear|column|col|show|ttitle|btitle)\b",
    re.I,
)


class PlsqlSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Num:
    text: str


@dataclass
class Call:
    name: str  # normalised, e.g. "ORDS.DEFINE_MODULE"
    args: dict[str, object]  # p_name (lower) -> str | Num | bool | None | list[str]
    text: str  # original statement text


@dataclass
class Raw:
    text: str


Statement = Call | Raw


@dataclass
class Script:
    header: list[str] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)


# --- tokenizer ----------------------------------------------------------------

Q_CLOSERS = {"[": "]", "{": "}", "(": ")", "<": ">"}


@dataclass
class Tok:
    kind: str  # str | num | id | qid | sym
    value: str
    start: int
    end: int


TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<line_comment>--[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<qstr>[nN]?[qQ]'(?P<qdelim>.))
  | (?P<str>[nN]?'(?:[^']|'')*')
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<qid>"[^"]+")
  | (?P<id>[A-Za-z][A-Za-z0-9_$#]*)
  | (?P<sym>=>|:=|\|\||\.\.|[^\sA-Za-z0-9])
    """,
    re.X | re.S,
)


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise PlsqlSyntaxError(f"cannot tokenize at offset {pos}: {text[pos:pos + 20]!r}")
        kind = m.lastgroup
        if kind == "qdelim":
            kind = "qstr"
        if kind == "qstr":
            delim = m.group("qdelim")
            close = Q_CLOSERS.get(delim, delim) + "'"
            end = text.find(close, m.end())
            if end < 0:
                raise PlsqlSyntaxError(f"unterminated q-quoted string at offset {pos}")
            toks.append(Tok("str", text[m.end():end], pos, end + 2))
            pos = end + 2
            continue
        if kind == "str":
            body = m.group()
            body = body[body.index("'") + 1:-1].replace("''", "'")
            toks.append(Tok("str", body, pos, m.end()))
        elif kind not in ("ws", "line_comment", "block_comment"):
            toks.append(Tok(kind, m.group(), pos, m.end()))
        pos = m.end()
    return toks


def _is_kw(tok: Tok, *words: str) -> bool:
    return tok.kind == "id" and tok.value.upper() in words


# --- block splitting ---------------------------------------------------------


def split_script(text: str) -> tuple[list[str], list[str]]:
    """Return (leading comment lines, PL/SQL blocks) from a SQL*Plus script."""
    lines = text.replace("\r\n", "\n").split("\n")
    header: list[str] = []
    blocks: list[str] = []
    i = 0
    while i < len(lines) and (lines[i].strip().startswith("--") or not lines[i].strip()):
        if lines[i].strip().startswith("--"):
            header.append(lines[i].strip()[2:].removeprefix(" ").rstrip())
        i += 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        word = stripped.split(None, 1)[0].upper() if stripped else ""
        if word.rstrip(";") in ("DECLARE", "BEGIN"):
            start = i
            while i < len(lines) and lines[i].strip() != "/":
                i += 1
            blocks.append("\n".join(lines[start:i]))
        elif word in ("EXEC", "EXECUTE"):
            blocks.append("BEGIN\n" + stripped.split(None, 1)[1].rstrip(";") + ";\nEND;")
        elif stripped and not stripped.startswith("--") and not SQLPLUS_COMMAND_RE.match(line) \
                and stripped != "/" and word.rstrip(";") != "COMMIT":
            raise PlsqlSyntaxError(f"line {i + 1}: unsupported top-level SQL: {stripped[:60]}")
        i += 1
    return header, blocks


# --- statements --------------------------------------------------------------

OPENERS = ("BEGIN", "IF", "LOOP", "CASE")


def _split_statements(toks: list[Tok]) -> list[list[Tok]]:
    """Split the executable section of a block into top-level statements."""
    stmts: list[list[Tok]] = []
    cur: list[Tok] = []
    depth = 0
    paren = 0
    prev_end = False
    for tok in toks:
        cur.append(tok)
        if tok.kind == "sym" and tok.value == "(":
            paren += 1
        elif tok.kind == "sym" and tok.value == ")":
            paren -= 1
        elif tok.kind == "id" and paren == 0:
            word = tok.value.upper()
            if word in OPENERS and not prev_end:
                depth += 1
            elif word == "END":
                depth -= 1
        prev_end = _is_kw(tok, "END")
        if tok.kind == "sym" and tok.value == ";" and paren == 0 and depth == 0:
            stmts.append(cur)
            cur = []
    if cur:
        raise PlsqlSyntaxError("statement without terminating ';'")
    return stmts


class _Unsupported(Exception):
    pass


def _eval_expr(toks: list[Tok], arrays: dict[str, dict[int, str]]) -> object:
    """Evaluate an argument expression: literal, array variable or concatenation."""
    if len(toks) == 1:
        t = toks[0]
        if t.kind == "str":
            return t.value
        if t.kind == "num":
            return Num(t.value)
        if _is_kw(t, "TRUE", "FALSE"):
            return t.value.upper() == "TRUE"
        if _is_kw(t, "NULL"):
            return None
        if t.kind == "id" and t.value.lower() in arrays:
            values = arrays[t.value.lower()]
            return [values[k] for k in sorted(values)]
    if len(toks) == 3 and _is_kw(toks[0], "ORDS") and toks[1].value == "." and toks[2].value.upper() in ORDS_CONSTANTS:
        return ORDS_CONSTANTS[toks[2].value.upper()]
    if len(toks) == 2 and toks[0].value == "-" and toks[1].kind == "num":
        return Num("-" + toks[1].value)
    # 'a' || chr(10) || to_clob('b') ...
    parts: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "str":
            parts.append(t.value)
            i += 1
        elif _is_kw(t, "CHR") and i + 3 < len(toks) and toks[i + 2].kind == "num" and toks[i + 3].value == ")":
            parts.append(chr(int(toks[i + 2].value)))
            i += 4
        elif _is_kw(t, "TO_CLOB") and i + 3 < len(toks) and toks[i + 2].kind == "str" and toks[i + 3].value == ")":
            parts.append(toks[i + 2].value)
            i += 4
        else:
            raise _Unsupported
        if i < len(toks):
            if toks[i].value != "||":
                raise _Unsupported
            i += 1
    if not parts:
        raise _Unsupported
    return "".join(parts)


def _split_args(toks: list[Tok]) -> list[list[Tok]]:
    args: list[list[Tok]] = [[]]
    paren = 0
    for t in toks:
        if t.value == "(" and t.kind == "sym":
            paren += 1
        elif t.value == ")" and t.kind == "sym":
            paren -= 1
        if t.kind == "sym" and t.value == "," and paren == 0:
            args.append([])
        else:
            args[-1].append(t)
    return [a for a in args if a] if args != [[]] else []


def _dotted_name(toks: list[Tok]) -> tuple[str, int] | None:
    """Parse `a.b.c` at the start of toks; return (NAME, tokens consumed)."""
    if not toks or toks[0].kind not in ("id", "qid"):
        return None
    names = [toks[0].value.strip('"')]
    i = 1
    while i + 1 < len(toks) and toks[i].value == "." and toks[i + 1].kind in ("id", "qid"):
        names.append(toks[i + 1].value.strip('"'))
        i += 2
    return ".".join(n.upper() for n in names), i


def _normalise_proc(name: str) -> str:
    return name.removeprefix("ORDS_METADATA.")


def _interpret(toks: list[Tok], text: str, arrays: dict[str, dict[int, str]]) -> Statement | None:
    body = toks[:-1]  # drop ';'
    dotted = _dotted_name(body)
    if dotted is None:
        return Raw(text)
    name, n = dotted

    if n == len(body):
        if name == "COMMIT":
            return None
        if name.endswith(".DELETE") and name.count(".") == 1 and name.split(".")[0].lower() in arrays:
            arrays[name.split(".")[0].lower()].clear()
            return None
        return Raw(text)

    # l_roles(1) := 'x'
    if (n == 1 and len(body) >= 6 and body[1].value == "(" and body[2].kind == "num"
            and body[3].value == ")" and body[4].value == ":=" and name.lower() in arrays):
        try:
            value = _eval_expr(body[5:], arrays)
        except _Unsupported:
            return Raw(text)
        if isinstance(value, str):
            arrays[name.lower()][int(body[2].value)] = value
            return None
        return Raw(text)

    if body[n].value != "(" or body[-1].value != ")":
        return Raw(text)
    args: dict[str, object] = {}
    for arg in _split_args(body[n + 1:-1]):
        if len(arg) < 3 or arg[0].kind != "id" or arg[1].value != "=>":
            return Raw(text)  # positional arguments: keep as written
        try:
            args[arg[0].value.lower()] = _eval_expr(arg[2:], arrays)
        except _Unsupported:
            return Raw(text)
    return Call(_normalise_proc(name), args, text)


def _parse_block(block: str) -> list[Statement]:
    toks = tokenize(block)
    arrays: dict[str, dict[int, str]] = {}
    i = 0
    if toks and _is_kw(toks[0], "DECLARE"):
        i = 1
        while i < len(toks) and not _is_kw(toks[i], "BEGIN"):
            # `name type ...;` declarations: remember VC_ARR-style collections
            j = i
            while j < len(toks) and toks[j].value != ";":
                j += 1
            decl = toks[i:j]
            if decl and decl[0].kind == "id" and any(_is_kw(t, "VC_ARR", "ARR", "TABLE") for t in decl):
                arrays[decl[0].value.lower()] = {}
            i = j + 1
    if i >= len(toks) or not _is_kw(toks[i], "BEGIN"):
        raise PlsqlSyntaxError("expected BEGIN")
    # executable section runs to the block's final END
    end = len(toks) - 1
    while end > i and toks[end].value == ";":
        end -= 1
    if not _is_kw(toks[end], "END"):
        raise PlsqlSyntaxError("block does not finish with END;")
    out: list[Statement] = []
    for stmt in _split_statements(toks[i + 1:end]):
        text = block[stmt[0].start:stmt[-1].end]
        result = _interpret(stmt, text, arrays)
        if result is not None:
            out.append(result)
    return out


def parse_script(text: str) -> Script:
    header, blocks = split_script(text)
    script = Script(header=header)
    for block in blocks:
        script.statements.extend(_parse_block(block))
    return script


# --- writing ------------------------------------------------------------------

MAX_LITERAL = 32000


def literal(value: object) -> str:
    match value:
        case None:
            return "NULL"
        case bool():
            return "TRUE" if value else "FALSE"
        case Num(text):
            return text
        case str():
            if len(value) <= MAX_LITERAL:
                return "'" + value.replace("'", "''") + "'"
            chunks = [value[i:i + MAX_LITERAL] for i in range(0, len(value), MAX_LITERAL)]
            return " ||\n".join("TO_CLOB('" + c.replace("'", "''") + "')" for c in chunks)
    raise TypeError(f"cannot write {value!r} as a PL/SQL literal")
