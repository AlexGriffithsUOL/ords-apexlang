// Thin client: the language features live in the Python language server
// (`python -m ords2apx lsp`); this file starts it and adds commands that run
// the ords2apx CLI.

import { execFile } from "node:child_process";
import * as path from "node:path";
import * as vscode from "vscode";
import { LanguageClient, LanguageClientOptions, ServerOptions } from "vscode-languageclient/node";

const LANGUAGE = "ords-apexlang";
const INSTALL_HINT = "Install the language server with: pip install 'ords2apx[lsp]'";

let client: LanguageClient | undefined;

function config() {
  return vscode.workspace.getConfiguration("ordsApexlang");
}

/** The Python interpreter that has ords2apx installed. */
function python(): string {
  return config().get<string>("pythonPath")?.trim() || (process.platform === "win32" ? "python" : "python3");
}

function grammarPath(): string | undefined {
  const configured = config().get<string>("grammarPath")?.trim();
  if (!configured) return undefined;
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  return path.resolve(folder, configured);
}

function runCli(args: string[], cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(python(), ["-m", "ords2apx", ...args], { cwd, maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        const missing = /No module named ords2apx/.test(stderr) || (err as NodeJS.ErrnoException).code === "ENOENT";
        reject(new Error(missing ? `ords2apx not found for ${python()}. ${INSTALL_HINT}` : stderr.trim() || err.message));
      } else {
        resolve(stdout);
      }
    });
  });
}

async function startClient(context: vscode.ExtensionContext): Promise<void> {
  const serverOptions: ServerOptions = { command: python(), args: ["-m", "ords2apx", "lsp"] };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ language: LANGUAGE }],
    initializationOptions: { grammarPath: grammarPath() },
    synchronize: { fileEvents: vscode.workspace.createFileSystemWatcher("**/*.ords.apx") },
  };
  client = new LanguageClient(LANGUAGE, "ORDS APEXlang", serverOptions, clientOptions);
  try {
    await client.start();
  } catch (e) {
    vscode.window.showErrorMessage(`ORDS APEXlang: the language server did not start (${(e as Error).message}). ${INSTALL_HINT}`);
  }
  context.subscriptions.push(client);
}

/** The file a command applies to: the explorer selection or the active editor. */
function target(uri?: vscode.Uri): vscode.Uri | undefined {
  const picked = uri ?? vscode.window.activeTextEditor?.document.uri;
  if (!picked) vscode.window.showErrorMessage("ORDS APEXlang: open or select a file first.");
  return picked;
}

async function showOutput(content: string, language: string) {
  const doc = await vscode.workspace.openTextDocument({ content, language });
  await vscode.window.showTextDocument(doc, { preview: false });
}

function command(name: string, run: (uri?: vscode.Uri) => Promise<void>): vscode.Disposable {
  return vscode.commands.registerCommand(`ordsApexlang.${name}`, async (uri?: vscode.Uri) => {
    try {
      await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: "ords2apx" }, () => run(uri));
    } catch (e) {
      vscode.window.showErrorMessage(`ORDS APEXlang: ${(e as Error).message}`);
    }
  });
}

async function askUrl(prompt: string): Promise<string | undefined> {
  const remembered = config().get<string>("ordsUrl") || undefined;
  return vscode.window.showInputBox({ prompt, value: remembered, placeHolder: "https://host/ords/hr/" });
}

function inTerminal(args: string[]) {
  const terminal = vscode.window.createTerminal("ords2apx");
  const quote = (s: string) => (/^[\w./:@=-]+$/.test(s) ? s : `"${s.replace(/(["\\$`])/g, "\\$1")}"`);
  terminal.show();
  terminal.sendText([python(), "-m", "ords2apx", ...args].map(quote).join(" "));
}

export async function activate(context: vscode.ExtensionContext) {
  await startClient(context);

  context.subscriptions.push(
    command("convert", async (uri) => {
      const file = target(uri);
      if (!file) return;
      await runCli([file.fsPath], path.dirname(file.fsPath));
      const result = file.fsPath.endsWith(".ords.apx")
        ? file.fsPath.slice(0, -".ords.apx".length) + ".sql"
        : file.fsPath.replace(/\.[^.]+$/, "") + ".ords.apx";
      await vscode.window.showTextDocument(vscode.Uri.file(result));
    }),
    command("split", async (uri) => {
      const file = target(uri);
      if (!file) return;
      const dir = await vscode.window.showInputBox({
        prompt: "Folder for the .ords.apx project",
        value: path.join(path.dirname(file.fsPath), "rest"),
      });
      if (!dir) return;
      const summary = await runCli(["split", file.fsPath, "-d", dir]);
      vscode.window.showInformationMessage(summary.trim() || `Split into ${dir}`);
    }),
    command("build", async (uri) => {
      const folder = uri ?? (await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false }))?.[0];
      if (!folder) return;
      await showOutput(await runCli(["build", folder.fsPath, "-o", "-"]), "sql");
    }),
    command("restSource", async (uri) => {
      const file = target(uri);
      const url = file && (await askUrl("ORDS URL of the schema, used for the APEX REST Data Source Server"));
      if (!file || !url) return;
      await showOutput(await runCli(["rest-source", file.fsPath, "--url", url, "-o", "-"]), "plaintext");
    }),
    command("openapi", async (uri) => {
      const file = target(uri);
      const url = file && (await askUrl("ORDS URL of the schema, used as the OpenAPI server"));
      if (!file || !url) return;
      await showOutput(await runCli(["openapi", file.fsPath, "--url", url, "-o", "-"]), "json");
    }),
    command("plan", async (uri) => {
      const folder = uri ?? vscode.workspace.workspaceFolders?.[0]?.uri;
      if (folder) inTerminal(["plan", folder.fsPath]);
    }),
    command("deploy", async (uri) => {
      const folder = uri ?? vscode.workspace.workspaceFolders?.[0]?.uri;
      if (folder) inTerminal(["deploy", folder.fsPath]);
    }),
    command("restartServer", async () => {
      await client?.stop();
      await startClient(context);
    }),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("ordsApexlang.pythonPath") || e.affectsConfiguration("ordsApexlang.grammarPath")) {
        await client?.stop();
        await startClient(context);
      }
    }),
  );
}

export async function deactivate() {
  await client?.stop();
}
