// Tokenises with VS Code's own TextMate engine (vscode-textmate + Oniguruma),
// so the generated grammar is checked the way VS Code will run it.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import * as oniguruma from "vscode-oniguruma";
import * as textmate from "vscode-textmate";

const ROOT = path.resolve(__dirname, "..", "..");
const GRAMMAR = path.join(ROOT, "syntaxes", "ords-apexlang.tmLanguage.json");
// stand-ins for VS Code's built-in grammars, enough to see that fences embed them
const STUBS: Record<string, object> = {
  "source.sql": { scopeName: "source.sql", patterns: [{ match: "(?i)\\b(select|from|where|begin|end)\\b", name: "keyword.other.sql" }] },
  "source.js": { scopeName: "source.js", patterns: [{ match: "\\b(const|return)\\b", name: "keyword.control.js" }] },
};

async function tokenizer() {
  const wasm = fs.readFileSync(require.resolve("vscode-oniguruma/release/onig.wasm"));
  await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) as ArrayBuffer);
  const registry = new textmate.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (p: string[]) => new oniguruma.OnigScanner(p),
      createOnigString: (s: string) => new oniguruma.OnigString(s),
    }),
    loadGrammar: async (scope: string) =>
      scope === "source.ords-apx"
        ? textmate.parseRawGrammar(fs.readFileSync(GRAMMAR, "utf8"), GRAMMAR)
        : STUBS[scope]
          ? textmate.parseRawGrammar(JSON.stringify(STUBS[scope]), `${scope}.json`)
          : null,
  });
  const grammar = (await registry.loadGrammar("source.ords-apx"))!;
  /** Map each non-blank token of `text` to its innermost scope. */
  return (text: string): Array<[string, string]> => {
    let stack = textmate.INITIAL;
    const out: Array<[string, string]> = [];
    for (const line of text.split("\n")) {
      const r = grammar.tokenizeLine(line, stack);
      stack = r.ruleStack;
      for (const t of r.tokens) {
        const s = line.slice(t.startIndex, t.endIndex);
        if (s.trim()) out.push([s.trim(), t.scopes[t.scopes.length - 1]]);
      }
    }
    return out;
  };
}

const scopeOf = (tokens: Array<[string, string]>, text: string) => tokens.find(([t]) => t === text)?.[1];

test("highlights components, properties, values and embedded code", async () => {
  const tokens = (await tokenizer())(
    [
      "// comment",
      'restModule hr.v1 (',
      "    status: published",
      "    itemsPerPage: 25",
      "    basepath: /m/",
      '    template "employees/:id" (',
      "        etag {",
      "            type: hash",
      "        }",
      "        handler GET (",
      "            source {",
      "                sqlQuery:",
      "                    ```sql",
      "                    select * from emp",
      "                    ```",
      "                javascriptCode:",
      "                    ```javascript",
      "                    const x = 1",
      "                    ```",
      "            }",
      "        )",
      "    )",
      ")",
      "restPrivilege p (",
      '    roles: [hr_role "SQL Developer" @r]',
      '    label: "25"',
      ")",
      "restWidget w (",
      ")",
    ].join("\n"),
  );
  assert.equal(scopeOf(tokens, "// comment"), "comment.line.double-slash.ords-apx");
  assert.equal(scopeOf(tokens, "restModule"), "keyword.other.component.ords-apx");
  assert.equal(scopeOf(tokens, "hr.v1"), "entity.name.type.component-id.ords-apx");
  assert.equal(scopeOf(tokens, "status"), "support.type.property-name.ords-apx");
  assert.equal(scopeOf(tokens, "published"), "support.constant.enum.ords-apx");
  assert.equal(scopeOf(tokens, "25"), "constant.numeric.ords-apx");
  assert.equal(scopeOf(tokens, "basepath"), "variable.other.property.ords-apx");
  assert.equal(scopeOf(tokens, "/m/"), "string.unquoted.ords-apx");
  assert.equal(scopeOf(tokens, "etag"), "storage.type.group.ords-apx");
  assert.equal(scopeOf(tokens, "select"), "keyword.other.sql");
  assert.equal(scopeOf(tokens, "const"), "keyword.control.js");
  assert.equal(scopeOf(tokens, "hr_role"), "string.unquoted.ords-apx");
  assert.equal(scopeOf(tokens, "SQL Developer"), "string.quoted.double.ords-apx");
  assert.equal(scopeOf(tokens, "@r"), "variable.other.constant.reference.ords-apx");
  assert.equal(scopeOf(tokens, "restWidget"), "variable.other.unknown-component.ords-apx");
});
