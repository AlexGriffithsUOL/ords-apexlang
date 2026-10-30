"""End-to-end test of the language server: run `ords2apx lsp` and talk JSON-RPC to it."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import pygls  # noqa: F401
except ImportError:  # the server is an optional extra
    pygls = None


class Client:
    def __init__(self, root: Path):
        self.proc = subprocess.Popen([sys.executable, "-m", "ords2apx", "lsp"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.next_id = 0
        self.notifications: list[dict] = []
        uri = root.as_uri()
        self.request("initialize", {"processId": None, "rootUri": uri, "capabilities": {},
                                    "workspaceFolders": [{"uri": uri, "name": "test"}]})
        self.notify("initialized", {})

    def send(self, message: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def read(self) -> dict:
        length = 0
        while (line := self.proc.stdout.readline().strip()):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        return json.loads(self.proc.stdout.read(length))

    def request(self, method: str, params: dict):
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        while True:
            msg = self.read()
            if msg.get("id") == self.next_id and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
            if "method" in msg and "id" in msg:  # server -> client request
                self.send({"id": msg["id"], "result": None})
            else:
                self.notifications.append(msg)

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params})

    def diagnostics(self, uri: str) -> list[dict]:
        # a round trip guarantees earlier notifications have arrived
        self.request("textDocument/hover", {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}})
        found = [n for n in self.notifications if n["method"] == "textDocument/publishDiagnostics"
                 and n["params"]["uri"] == uri]
        return found[-1]["params"]["diagnostics"] if found else []

    def close(self) -> None:
        self.request("shutdown", {})
        self.notify("exit", {})
        self.proc.wait(timeout=10)
        self.proc.stdin.close()
        self.proc.stdout.close()


@unittest.skipIf(pygls is None, "pygls not installed (pip install ords2apx[lsp])")
class LanguageServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "modules").mkdir()
        (root / "modules" / "hr.ords.apx").write_text("restModule hr (\n    basePath: /hr/\n)\n")
        self.security = root / "security.ords.apx"
        self.security.write_text("")
        self.client = Client(root)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def open(self, text: str) -> str:
        uri = self.security.as_uri()
        self.client.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "ords-apexlang", "version": 1, "text": text}})
        return uri

    def test_diagnostics_resolve_references_across_files(self):
        uri = self.open("restPrivilege p (\n    modules: [@hr @nope]\n    status: x\n)\n")
        messages = [d["message"] for d in self.client.diagnostics(uri)]
        self.assertEqual(messages, ["no component named 'nope' in this project",
                                    "'status' is not a known restPrivilege property; it will be passed to ORDS as p_status."])

    def test_completion_hover_formatting_definition(self):
        uri = self.open("restPrivilege p (\n    modules: [\n  label:   x\n)\n")
        doc = {"textDocument": {"uri": uri}}
        items = self.client.request("textDocument/completion", {**doc, "position": {"line": 1, "character": 14}})
        self.assertEqual([i["label"] for i in items["items"]], [])  # modules complete @ only for this file's modules
        self.client.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [
            {"text": "restPrivilege p (\n  label:   x\n  modules: [@hr]\n)\n"}]})
        hover = self.client.request("textDocument/hover", {**doc, "position": {"line": 1, "character": 3}})
        self.assertIn("p_label", hover["contents"]["value"])
        edits = self.client.request("textDocument/formatting", {**doc, "options": {"tabSize": 4, "insertSpaces": True}})
        self.assertEqual(edits[0]["newText"], "restPrivilege p (\n    label: x\n    modules: [@hr]\n)\n")
        loc = self.client.request("textDocument/definition", {**doc, "position": {"line": 2, "character": 14}})
        self.assertTrue(loc["uri"].endswith("modules/hr.ords.apx"))
        self.assertEqual(loc["range"]["start"], {"line": 0, "character": 11})


if __name__ == "__main__":
    unittest.main()
