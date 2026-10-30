"""ords2apx command line.

    ords2apx FILE                      convert: ORDS export (.sql) <-> .ords.apx
    ords2apx check PATH...             validate .ords.apx files or project folders
    ords2apx split EXPORT -d DIR       ORDS export -> project folder (one file per module)
    ords2apx build DIR [-o OUT]        project folder -> one deployable ORDS script
    ords2apx rest-source PATH --url U  APEX REST Data Sources (APEXlang) for the modules
    ords2apx openapi PATH --url U      OpenAPI 3 document for the modules
    ords2apx pull -d DIR               database -> project folder
    ords2apx plan DIR                  compare a project folder with the database
    ords2apx deploy DIR                apply the differences to the database
    ords2apx lsp                       language server over stdin/stdout
    ords2apx textmate                  TextMate grammar for highlighting
    ords2apx reference                 Markdown reference of every component

Output files default to the input's name with the other extension; `-o -`
writes to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, apx
from .apx import ApxSyntaxError
from .model import ConversionError, documents_to_sql, sql_to_apx, sql_to_document
from .plsql import PlsqlSyntaxError
from .project import SUFFIX, ProjectError, load, merge, project_files, write_split

COMMANDS = {"convert", "check", "split", "build", "rest-source", "openapi", "pull", "plan", "deploy", "lsp", "textmate",
            "reference"}


def _write(text: str, out: str | None, default: Path | None) -> None:
    if out == "-" or (out is None and default is None):
        sys.stdout.write(text)
        return
    dest = Path(out) if out else default
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"wrote {dest}", file=sys.stderr)


def _is_apx(path: Path) -> bool:
    return path.name.endswith(".apx")


def _stem(path: Path) -> str:
    return path.name[: -len(SUFFIX)] if path.name.endswith(SUFFIX) else path.stem


def _warn(e: Exception) -> None:
    print(f"warning: {e}", file=sys.stderr)


def _paths(path: Path) -> list[Path]:
    if path.is_dir():
        found = project_files(path)
        if not found:
            raise ProjectError(f"no {SUFFIX} files in {path}")
        return found
    return [path]


# --- commands -------------------------------------------------------------------


def cmd_convert(args) -> int:
    src: Path = args.file
    if src.is_dir():
        return cmd_build(argparse.Namespace(dir=src, output=args.output))
    text = src.read_text(encoding="utf-8")
    if _is_apx(src):
        result = documents_to_sql([apx.loads(text)], f"Generated from {src.name} by ords2apx", _warn)
        default = src.with_name(_stem(src) + ".sql")
    else:
        result = sql_to_apx(text, src.name)
        default = src.with_name(src.stem + SUFFIX)
    if args.output is None and default.resolve() == src.resolve():
        raise ProjectError(f"output would overwrite {src}")
    _write(result, args.output, default)
    return 0


def cmd_check(args) -> int:
    from .analysis import validate
    from .schema import load_schema

    schema = load_schema(args.grammar)
    files = [p for path in args.paths for p in _paths(path)]
    docs = {p: apx.parse(p.read_text(encoding="utf-8")) for p in files}
    # @references resolve across every file checked together
    targets = {c.id: c for d in docs.values() for c in d.components if c.id is not None}
    errors = warnings = 0
    for path, doc in docs.items():
        for problem in validate(doc, schema, targets):
            span = problem.span
            where = f"{path}:{span.line + 1}:{span.start + 1}" if span else str(path)
            print(f"{where}: {problem.severity}: {problem.message}")
            errors += problem.severity == "error"
            warnings += problem.severity == "warning"
    print(f"{len(files)} file(s): {errors} error(s), {warnings} warning(s)", file=sys.stderr)
    return 1 if errors or (args.strict and warnings) else 0


def cmd_split(args) -> int:
    src: Path = args.export
    text = src.read_text(encoding="utf-8")
    doc = apx.loads(text) if _is_apx(src) else sql_to_document(text, src.name)
    written = write_split(doc, args.dir, src.name, args.force)
    print(f"split {src} into {len(written)} file(s) under {args.dir}")
    return 0


def cmd_build(args) -> int:
    docs = [d for _, d in load(args.dir)]
    _write(documents_to_sql([merge(docs)], f"Built from {args.dir} by ords2apx", _warn), args.output, None)
    return 0


def cmd_rest_source(args) -> int:
    from .restsource import generate
    files, skipped = generate([d for _, d in load(args.path)], args.url, args.server_id)
    for s in skipped:
        print(f"skipped {s}", file=sys.stderr)
    if args.app:
        for rel, text in files.items():
            _write(text, None, args.app / rel)
    else:
        _write("\n".join(f"// ===== {rel}\n{text}" for rel, text in files.items()), args.output or "-", None)
    return 0


def cmd_openapi(args) -> int:
    from .openapi import dumps, generate
    _write(dumps(generate([d for _, d in load(args.path)], args.url, args.title)), args.output, None)
    return 0


def cmd_pull(args) -> int:
    from .db import connect, remote_document
    with connect(args.dsn) as conn:
        doc = remote_document(conn)
    written = write_split(doc, args.dir, "the database", args.force)
    print(f"pulled {len(doc.components)} component(s) into {len(written)} file(s) under {args.dir}")
    return 0


def cmd_plan(args, deploy: bool = False) -> int:
    from .db import connect, deploy_block, execute, format_plan, plan, remote_document
    local = [d for _, d in load(args.dir)]
    with connect(args.dsn) as conn:
        changes = plan(local, remote_document(conn))
        print(format_plan(changes, args.prune))
        if not deploy:
            # removals only count when --prune would make deploy carry them out
            actionable = [c for c in changes if c.action != "remove" or args.prune]
            return 2 if args.detailed_exitcode and actionable else 0
        block = deploy_block(changes, args.prune)
        if block is None:
            return 0
        if args.sql:
            _write(f"-- Deployment generated by ords2apx\n\nSET DEFINE OFF\n\n{block}\n/\n", args.sql, None)
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                raise ProjectError("refusing to deploy without confirmation; pass --yes")
            if input("\nApply these changes? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return 1
        execute(conn, block)
        print("Deployed.")
    return 0


def cmd_lsp(args) -> int:
    try:
        from .lsp import main as lsp_main
    except ImportError:
        raise ProjectError("the language server needs pygls: pip install 'ords2apx[lsp]'") from None
    lsp_main()
    return 0


def cmd_reference(args) -> int:
    from .reference import generate
    from .schema import load_schema
    sys.stdout.write(generate(load_schema(args.grammar)))
    return 0


def cmd_textmate(args) -> int:
    from .schema import load_schema
    from .textmate import dumps
    sys.stdout.write(dumps(load_schema(args.grammar)))
    return 0


# --- argument parsing ---------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ords2apx", description="ORDS REST modules as APEXlang (.ords.apx) files.")
    p.add_argument("--version", action="version", version=f"ords2apx {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    c = sub.add_parser("convert", help="ORDS export (.sql) <-> .ords.apx; also the default command")
    c.add_argument("file", type=Path)
    c.add_argument("-o", "--output", help="output path, or - for stdout")
    c.set_defaults(run=cmd_convert)

    c = sub.add_parser("check", help="validate .ords.apx files or project folders")
    c.add_argument("paths", type=Path, nargs="+")
    c.add_argument("--strict", action="store_true", help="fail on warnings too")
    c.add_argument("--grammar", help="use this grammar instead of the bundled one")
    c.set_defaults(run=cmd_check)

    c = sub.add_parser("split", help="ORDS export -> project folder, one file per module")
    c.add_argument("export", type=Path)
    c.add_argument("-d", "--dir", type=Path, default=Path("rest"))
    c.add_argument("--force", action="store_true", help="overwrite existing .ords.apx files")
    c.set_defaults(run=cmd_split)

    c = sub.add_parser("build", help="project folder -> one deployable ORDS script")
    c.add_argument("dir", type=Path)
    c.add_argument("-o", "--output", help="output path (default: stdout)")
    c.set_defaults(run=cmd_build)

    c = sub.add_parser("rest-source", help="APEX REST Data Sources for the modules (APEXlang)")
    c.add_argument("path", type=Path, help=".ords.apx file or project folder")
    c.add_argument("--url", required=True, help="ORDS URL of the schema, e.g. https://host/ords/hr/")
    c.add_argument("--app", type=Path, help="APEX application folder to write into")
    c.add_argument("--server-id", help="id of the restDataSourceServer (default: from the URL)")
    c.add_argument("-o", "--output", help="output path when not using --app (default: stdout)")
    c.set_defaults(run=cmd_rest_source)

    c = sub.add_parser("openapi", help="OpenAPI 3 document for the modules")
    c.add_argument("path", type=Path, help=".ords.apx file or project folder")
    c.add_argument("--url", required=True, help="ORDS URL of the schema, e.g. https://host/ords/hr/")
    c.add_argument("--title")
    c.add_argument("-o", "--output", help="output path (default: stdout)")
    c.set_defaults(run=cmd_openapi)

    def db_args(c):
        c.add_argument("--dsn", help="user/password@host:port/service (default: $ORDS2APX_DSN)")

    c = sub.add_parser("pull", help="database -> project folder")
    c.add_argument("-d", "--dir", type=Path, default=Path("rest"))
    c.add_argument("--force", action="store_true", help="overwrite existing .ords.apx files")
    db_args(c)
    c.set_defaults(run=cmd_pull)

    c = sub.add_parser("plan", help="compare a project with the database")
    c.add_argument("dir", type=Path)
    c.add_argument("--prune", action="store_true", help="also remove what the project no longer defines")
    c.add_argument("--detailed-exitcode", action="store_true", help="exit 2 when there are changes")
    db_args(c)
    c.set_defaults(run=cmd_plan)

    c = sub.add_parser("deploy", help="apply a project's differences to the database")
    c.add_argument("dir", type=Path)
    c.add_argument("--prune", action="store_true", help="also remove what the project no longer defines")
    c.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    c.add_argument("--sql", help="write the deployment script here instead of running it")
    db_args(c)
    c.set_defaults(run=lambda a: cmd_plan(a, deploy=True))

    c = sub.add_parser("lsp", help="language server over stdin/stdout")
    c.set_defaults(run=cmd_lsp)

    c = sub.add_parser("reference", help="Markdown reference of every component and property")
    c.add_argument("--grammar", help="use this grammar instead of the bundled one")
    c.set_defaults(run=cmd_reference)

    c = sub.add_parser("textmate", help="TextMate grammar for highlighting")
    c.add_argument("--grammar", help="use this grammar instead of the bundled one")
    c.set_defaults(run=cmd_textmate)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # `ords2apx FILE` is short for `ords2apx convert FILE`
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["convert", *argv]
    args = parser().parse_args(argv)
    from .db import DatabaseError
    try:
        return args.run(args)
    except (ApxSyntaxError, ConversionError, PlsqlSyntaxError, ProjectError, DatabaseError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
