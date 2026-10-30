import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { completions, contextAt, definition, hover, validate, walk } from "./analysis";
import { ApxComponent, ApxDocument, parseApx, Span } from "./apxDocument";
import { parseEbnf } from "./ebnf";
import { buildSchema, Schema } from "./schema";

const LANGUAGE = "ords-apexlang";

const COMPLETION_KINDS = {
  component: vscode.CompletionItemKind.Class,
  property: vscode.CompletionItemKind.Property,
  group: vscode.CompletionItemKind.Module,
  value: vscode.CompletionItemKind.EnumMember,
  reference: vscode.CompletionItemKind.Reference,
} as const;

const toRange = (s: Span) => new vscode.Range(s.line, s.start, s.line, s.end);

export function activate(context: vscode.ExtensionContext) {
  const output = vscode.window.createOutputChannel("ORDS APEXlang");
  const diagnostics = vscode.languages.createDiagnosticCollection(LANGUAGE);
  let schema: Schema | undefined;
  let grammarPath = "";

  const parsed = new Map<string, { version: number; doc: ApxDocument }>();
  const parse = (document: vscode.TextDocument): ApxDocument => {
    const key = document.uri.toString();
    const cached = parsed.get(key);
    if (cached && cached.version === document.version) return cached.doc;
    const doc = parseApx(document.getText());
    parsed.set(key, { version: document.version, doc });
    return doc;
  };

  const refresh = (document: vscode.TextDocument) => {
    if (document.languageId !== LANGUAGE || !schema) return;
    diagnostics.set(
      document.uri,
      validate(parse(document), schema).map((p) => {
        const d = new vscode.Diagnostic(
          toRange(p.span),
          p.message,
          p.severity === "error" ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning,
        );
        d.source = "ords-apexlang";
        return d;
      }),
    );
  };

  const loadSchema = () => {
    const configured = vscode.workspace.getConfiguration("ordsApexlang").get<string>("grammarPath")?.trim();
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    grammarPath = configured
      ? path.resolve(folder ?? "", configured)
      : context.asAbsolutePath(path.join("grammar", "ords-apexlang.ebnf"));
    try {
      schema = buildSchema(parseEbnf(fs.readFileSync(grammarPath, "utf8")));
      output.appendLine(`Loaded ${schema.components.size} components from ${grammarPath}`);
    } catch (e) {
      const message = `ORDS APEXlang: could not load grammar ${grammarPath}: ${(e as Error).message}`;
      output.appendLine(message);
      vscode.window.showErrorMessage(message + (schema ? " (keeping the previous grammar)" : ""));
    }
    vscode.workspace.textDocuments.forEach(refresh);
  };

  loadSchema();

  const timers = new Map<string, NodeJS.Timeout>();
  context.subscriptions.push(
    output,
    diagnostics,
    vscode.workspace.onDidOpenTextDocument(refresh),
    vscode.workspace.onDidChangeTextDocument((e) => {
      const key = e.document.uri.toString();
      clearTimeout(timers.get(key));
      timers.set(key, setTimeout(() => refresh(e.document), 150));
    }),
    vscode.workspace.onDidCloseTextDocument((d) => {
      diagnostics.delete(d.uri);
      parsed.delete(d.uri.toString());
    }),
    vscode.workspace.onDidSaveTextDocument((d) => {
      if (path.resolve(d.uri.fsPath) === path.resolve(grammarPath)) loadSchema();
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("ordsApexlang.grammarPath")) loadSchema();
    }),

    vscode.languages.registerCompletionItemProvider(
      LANGUAGE,
      {
        provideCompletionItems(document, position, _token, ctx) {
          if (!schema) return undefined;
          const doc = parse(document);
          // after a space or '[' only complete values, not whole properties
          if (ctx.triggerKind === vscode.CompletionTriggerKind.TriggerCharacter && contextAt(doc, position.line, position.character).type !== "value") {
            return undefined;
          }
          const word = document.getWordRangeAtPosition(position, /@?[A-Za-z0-9_][A-Za-z0-9_.\-]*/);
          return completions(doc, schema, position.line, position.character).map((c) => {
            const item = new vscode.CompletionItem(c.label, COMPLETION_KINDS[c.kind]);
            item.insertText = new vscode.SnippetString(c.insertText);
            item.detail = c.detail;
            if (c.documentation) item.documentation = new vscode.MarkdownString(c.documentation);
            item.sortText = c.sortText;
            if (word) item.range = word;
            return item;
          });
        },
      },
      " ",
      "@",
      "[",
    ),

    vscode.languages.registerHoverProvider(LANGUAGE, {
      provideHover(document, position) {
        if (!schema) return undefined;
        const h = hover(parse(document), schema, position.line, position.character);
        return h && new vscode.Hover(new vscode.MarkdownString(h.markdown), toRange(h.span));
      },
    }),

    vscode.languages.registerDefinitionProvider(LANGUAGE, {
      provideDefinition(document, position) {
        const target = definition(parse(document), position.line, position.character);
        return target && new vscode.Location(document.uri, toRange(target.idSpan ?? target.kindSpan));
      },
    }),

    vscode.languages.registerDocumentSymbolProvider(LANGUAGE, {
      provideDocumentSymbols(document) {
        const doc = parse(document);
        const lastLine = document.lineCount - 1;
        const symbol = (c: ApxComponent): vscode.DocumentSymbol => {
          const end = Math.min(c.endLine, lastLine);
          const s = new vscode.DocumentSymbol(
            c.id ?? c.kind,
            c.id !== undefined ? c.kind : "",
            c.children.length ? vscode.SymbolKind.Namespace : vscode.SymbolKind.Object,
            new vscode.Range(c.startLine, 0, end, document.lineAt(end).text.length),
            toRange(c.idSpan ?? c.kindSpan),
          );
          s.children = c.children.map(symbol);
          return s;
        };
        return doc.components.map(symbol);
      },
    }),

    vscode.languages.registerWorkspaceSymbolProvider({
      provideWorkspaceSymbols(query) {
        const out: vscode.SymbolInformation[] = [];
        for (const document of vscode.workspace.textDocuments) {
          if (document.languageId !== LANGUAGE) continue;
          for (const c of walk(parse(document).components)) {
            if (c.id === undefined || !c.id.toLowerCase().includes(query.toLowerCase())) continue;
            out.push(new vscode.SymbolInformation(c.id, vscode.SymbolKind.Object, c.kind,
              new vscode.Location(document.uri, toRange(c.idSpan ?? c.kindSpan))));
          }
        }
        return out;
      },
    }),
  );
}

export function deactivate() {}
