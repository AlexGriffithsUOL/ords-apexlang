"""ords2apx command line.

    ords2apx export.sql              ORDS export -> export.apx
    ords2apx module.apx              APEXlang    -> module.sql
    ords2apx check module.apx        validate only
    ords2apx export.sql -o -         write to stdout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .apx import ApxSyntaxError
from .model import ConversionError, apx_to_sql, document_to_calls, sql_to_apx
from .plsql import PlsqlSyntaxError
from . import apx


def _convert(src: Path, out: str | None) -> int:
    text = src.read_text(encoding="utf-8")
    if src.suffix.lower() == ".apx":
        result, ext = apx_to_sql(text, src.name), ".sql"
    else:
        result, ext = sql_to_apx(text, src.name), ".apx"
    if out == "-":
        sys.stdout.write(result)
        return 0
    dest = Path(out) if out else src.with_suffix(ext)
    if dest.resolve() == src.resolve():
        print(f"error: output would overwrite {src}", file=sys.stderr)
        return 1
    dest.write_text(result, encoding="utf-8")
    print(f"{src} -> {dest}", file=sys.stderr)
    return 0


def _check(src: Path) -> int:
    warnings: list[Exception] = []
    document_to_calls(apx.loads(src.read_text(encoding="utf-8")), warnings.append)
    for w in warnings:
        print(f"{src}: warning: {w}", file=sys.stderr)
    print(f"{src}: ok", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check = bool(argv) and argv[0] == "check"
    parser = argparse.ArgumentParser(
        prog="ords2apx",
        description="Convert ORDS REST export scripts to APEXlang-style .apx files and back.",
    )
    if check:
        parser.add_argument("check")
        parser.add_argument("file", type=Path, help=".apx file to validate")
    else:
        parser.add_argument("file", type=Path, help=".sql ORDS export, or .apx file")
        parser.add_argument("-o", "--output", help="output path, or - for stdout")
    args = parser.parse_args(argv)
    try:
        return _check(args.file) if check else _convert(args.file, args.output)
    except (ApxSyntaxError, ConversionError, PlsqlSyntaxError) as e:
        print(f"{args.file}: error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
