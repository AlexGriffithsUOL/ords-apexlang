"""Language server for .ords.apx files.

    ords2apx lsp            speak LSP over stdin/stdout

Diagnostics, completion, hover, go to definition, find references, document
and workspace symbols, and formatting, all driven by the grammar. @references
resolve across every .ords.apx file in the workspace, so a privilege in
security.ords.apx can point at a module in modules/hr.v1.ords.apx.

Initialization options (all optional):
    {"grammarPath": "path/to/ords-apexlang.ebnf"}   use this grammar; saving it reloads
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path, to_fs_path

from . import __version__, apx
from .analysis import completions, context_at, definition, format_document, hover, reference_targets, validate
from .apx import Component, Document, Span
from .schema import Schema, default_schema, load_schema

SUFFIX = ".ords.apx"
log = logging.getLogger(__name__)

COMPLETION_KINDS = {
    "component": lsp.CompletionItemKind.Class,
    "property": lsp.CompletionItemKind.Property,
    "group": lsp.CompletionItemKind.Module,
    "value": lsp.CompletionItemKind.EnumMember,
    "reference": lsp.CompletionItemKind.Reference,
}


def _range(s: Span) -> lsp.Range:
    return lsp.Range(lsp.Position(s.line, s.start), lsp.Position(s.line, s.end))


class OrdsApxServer(LanguageServer):
    def __init__(self):
        super().__init__("ords-apexlang", __version__)
        self.schema: Schema = default_schema()
        self.grammar_path: str | None = None
        # every .ords.apx in the workspace: uri -> (mtime or None for open documents, parsed)
        self.index: dict[str, tuple[float | None, Document]] = {}

    # --- documents -----------------------------------------------------------

    def load_grammar(self, path: str | None) -> None:
        self.grammar_path = os.path.abspath(path) if path else None
        try:
            self.schema = load_schema(self.grammar_path) if self.grammar_path else default_schema()
        except Exception as e:  # keep the previous grammar
            self.window_show_message(lsp.ShowMessageParams(lsp.MessageType.Error, f"ORDS APEXlang: grammar error: {e}"))

    def parse_open(self, uri: str) -> Document:
        doc = apx.parse(self.workspace.get_text_document(uri).source)
        self.index[uri] = (None, doc)
        return doc

    def document(self, uri: str) -> Document:
        cached = self.index.get(uri)
        if cached and cached[0] is None:
            return cached[1]
        return self.parse_open(uri)

    def scan_workspace(self) -> None:
        """Index .ords.apx files on disk (open documents take precedence)."""
        for folder in self.workspace.folders.values():
            root = to_fs_path(folder.uri)
            if not root:
                continue
            for path in Path(root).rglob(f"*{SUFFIX}"):
                if any(part.startswith(".") or part == "node_modules" for part in path.parts):
                    continue
                uri = from_fs_path(str(path))
                cached = self.index.get(uri)
                if cached and cached[0] is None:
                    continue
                mtime = path.stat().st_mtime
                if not cached or cached[0] != mtime:
                    try:
                        self.index[uri] = (mtime, apx.parse(path.read_text(encoding="utf-8")))
                    except OSError:
                        pass

    def project_targets(self, exclude: str) -> dict[str, tuple[str, Component]]:
        out: dict[str, tuple[str, Component]] = {}
        for uri, (_, doc) in self.index.items():
            if uri != exclude:
                for cid, c in reference_targets(doc).items():
                    out.setdefault(cid, (uri, c))
        return out

    def publish(self, uri: str) -> None:
        doc = self.document(uri)
        targets = {k: c for k, (_, c) in self.project_targets(uri).items()}
        diagnostics = [
            lsp.Diagnostic(
                range=_range(p.span),
                message=p.message,
                severity=lsp.DiagnosticSeverity.Error if p.severity == "error" else lsp.DiagnosticSeverity.Warning,
                source="ords-apexlang",
            )
            for p in validate(doc, self.schema, targets)
            if p.span is not None
        ]
        self.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))

    def publish_open(self) -> None:
        for uri in list(self.workspace.text_documents):
            if uri.endswith(SUFFIX):
                self.publish(uri)


server = OrdsApxServer()


@server.feature(lsp.INITIALIZE)
def initialize(ls: OrdsApxServer, params: lsp.InitializeParams) -> None:
    options = params.initialization_options or {}
    if isinstance(options, dict) and options.get("grammarPath"):
        ls.load_grammar(options["grammarPath"])


@server.feature(lsp.INITIALIZED)
def initialized(ls: OrdsApxServer, params: lsp.InitializedParams) -> None:
    ls.scan_workspace()


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: OrdsApxServer, params: lsp.DidOpenTextDocumentParams) -> None:
    ls.parse_open(params.text_document.uri)
    ls.publish(params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: OrdsApxServer, params: lsp.DidChangeTextDocumentParams) -> None:
    ls.parse_open(params.text_document.uri)
    ls.publish(params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: OrdsApxServer, params: lsp.DidSaveTextDocumentParams) -> None:
    path = to_fs_path(params.text_document.uri)
    if ls.grammar_path and path and os.path.abspath(path) == ls.grammar_path:
        ls.load_grammar(ls.grammar_path)
    # ids defined here may resolve references elsewhere
    ls.publish_open()


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: OrdsApxServer, params: lsp.DidCloseTextDocumentParams) -> None:
    ls.index.pop(params.text_document.uri, None)
    ls.scan_workspace()
    ls.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[]))


@server.feature(lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES)
def watched_files(ls: OrdsApxServer, params: lsp.DidChangeWatchedFilesParams) -> None:
    for change in params.changes:
        if change.type == lsp.FileChangeType.Deleted:
            ls.index.pop(change.uri, None)
    ls.scan_workspace()
    ls.publish_open()


@server.feature(lsp.TEXT_DOCUMENT_COMPLETION, lsp.CompletionOptions(trigger_characters=[" ", "@", "["]))
def completion(ls: OrdsApxServer, params: lsp.CompletionParams) -> lsp.CompletionList | None:
    doc = ls.document(params.text_document.uri)
    pos = params.position
    # after a space or '[' only complete values, not whole properties
    if (params.context and params.context.trigger_kind == lsp.CompletionTriggerKind.TriggerCharacter
            and context_at(doc, pos.line, pos.character).type != "value"):
        return None
    items = []
    for c in completions(doc, ls.schema, pos.line, pos.character):
        items.append(lsp.CompletionItem(
            label=c.label,
            kind=COMPLETION_KINDS[c.kind],
            detail=c.detail,
            documentation=lsp.MarkupContent(lsp.MarkupKind.Markdown, c.documentation) if c.documentation else None,
            insert_text=c.insert_text,
            insert_text_format=lsp.InsertTextFormat.Snippet,
            sort_text=c.sort_text,
        ))
    return lsp.CompletionList(is_incomplete=False, items=items)


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def on_hover(ls: OrdsApxServer, params: lsp.HoverParams) -> lsp.Hover | None:
    h = hover(ls.document(params.text_document.uri), ls.schema, params.position.line, params.position.character)
    return h and lsp.Hover(lsp.MarkupContent(lsp.MarkupKind.Markdown, h.markdown), _range(h.span))


def _location(uri: str, c: Component) -> lsp.Location:
    return lsp.Location(uri, _range(c.id_span or c.kind_span))


@server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def on_definition(ls: OrdsApxServer, params: lsp.DefinitionParams) -> lsp.Location | None:
    uri, pos = params.text_document.uri, params.position
    doc = ls.document(uri)
    target = definition(doc, pos.line, pos.character)
    if target is not None:
        return _location(uri, target)
    # the reference may point into another file
    for c in doc.walk():
        for v in [*c.props.values(), *(v for g in c.groups.values() for v in g.props.values())]:
            items = v.items if isinstance(v, apx.Array) else (v,)
            for item in items:
                if isinstance(item, apx.Reference) and item.span and item.span.line == pos.line \
                        and item.span.start <= pos.character <= item.span.end:
                    found = ls.project_targets(uri).get(item.target)
                    return _location(*found) if found else None
    return None


@server.feature(lsp.TEXT_DOCUMENT_REFERENCES)
def on_references(ls: OrdsApxServer, params: lsp.ReferenceParams) -> list[lsp.Location]:
    uri, pos = params.text_document.uri, params.position
    doc = ls.document(uri)
    target = next((c for c in doc.components if c.id_span and c.id_span.line == pos.line
                   and c.id_span.start <= pos.character <= c.id_span.end), None) or definition(doc, pos.line, pos.character)
    if target is None or target.id is None:
        return []
    ls.scan_workspace()
    out = []
    for other, (_, odoc) in ls.index.items():
        # references() finds uses of the id under the cursor; reuse it per file
        for c in odoc.walk():
            for v in [*c.props.values(), *(v for g in c.groups.values() for v in g.props.values())]:
                for item in (v.items if isinstance(v, apx.Array) else (v,)):
                    if isinstance(item, apx.Reference) and item.target == target.id and item.span:
                        out.append(lsp.Location(other, _range(item.span)))
    if params.context.include_declaration:
        out.insert(0, _location(uri, target))
    return out


def _symbol(c: Component, lines: list[str]) -> lsp.DocumentSymbol:
    end = min(c.end_line, len(lines) - 1)
    return lsp.DocumentSymbol(
        name=c.id if c.id is not None else c.kind,
        detail=c.kind if c.id is not None else "",
        kind=lsp.SymbolKind.Namespace if c.children else lsp.SymbolKind.Object,
        range=lsp.Range(lsp.Position(c.kind_span.line, 0), lsp.Position(end, len(lines[end]))),
        selection_range=_range(c.id_span or c.kind_span),
        children=[_symbol(ch, lines) for ch in c.children],
    )


@server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbols(ls: OrdsApxServer, params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol]:
    doc = ls.document(params.text_document.uri)
    return [_symbol(c, doc.lines) for c in doc.components]


@server.feature(lsp.WORKSPACE_SYMBOL)
def workspace_symbols(ls: OrdsApxServer, params: lsp.WorkspaceSymbolParams) -> list[lsp.WorkspaceSymbol]:
    ls.scan_workspace()
    query = params.query.lower()
    return [
        lsp.WorkspaceSymbol(name=c.id, kind=lsp.SymbolKind.Object, container_name=c.kind, location=_location(uri, c))
        for uri, (_, doc) in ls.index.items()
        for c in doc.walk()
        if c.id is not None and query in c.id.lower()
    ]


@server.feature(lsp.TEXT_DOCUMENT_FORMATTING)
def formatting(ls: OrdsApxServer, params: lsp.DocumentFormattingParams) -> list[lsp.TextEdit] | None:
    text = ls.workspace.get_text_document(params.text_document.uri).source
    formatted = format_document(text)
    if formatted is None or formatted == text:
        return None
    lines = text.split("\n")
    return [lsp.TextEdit(lsp.Range(lsp.Position(0, 0), lsp.Position(len(lines), 0)), formatted)]


@server.command("ords2apx.reloadGrammar")
def reload_grammar(ls: OrdsApxServer, *args) -> None:
    ls.load_grammar(ls.grammar_path)
    ls.publish_open()


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    server.start_io()
