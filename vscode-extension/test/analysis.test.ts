import assert from "node:assert/strict";
import { test } from "node:test";
import { completions, contextAt, definition, hover, validate } from "../src/analysis";
import { parseApx } from "../src/apxDocument";
import { example, schema, withCursor } from "./helpers";

const problems = (text: string) => validate(parseApx(text), schema).map((p) => `${p.span.line}:${p.severity}: ${p.message}`);
const labels = (text: string) => {
  const { doc, line, character } = withCursor(text);
  return completions(doc, schema, line, character).map((c) => c.label);
};

test("the converter's example output is valid", () => {
  assert.deepEqual(problems(example()), []);
});

test("structural problems", () => {
  assert.deepEqual(problems("restModul m (\n)\n"), ["0:error: unknown component 'restModul'. Did you mean 'restModule'?"]);
  assert.deepEqual(problems("restModule m (\n\n    handler GET (\n    )\n\n)\n"), [
    "2:error: 'handler' cannot be nested inside restModule (allowed: template)",
  ]);
  assert.deepEqual(problems("template t (\n)\n"), ["0:error: 'template' must be nested inside restModule"]);
  assert.deepEqual(problems("restModule (\n)\n"), ["0:error: restModule needs an id (p_module_name), e.g. restModule name ("]);
  assert.deepEqual(problems("restModule m (\n    basePath: /m/\n"), ["0:error: missing closing ')'"]);
  assert.deepEqual(problems("restModule m (\n    oops\n)\n"), [
    "1:error: 'oops' needs a value ('oops: …'), a body ('oops (' / 'oops {')",
  ]);
});

test("property problems", () => {
  assert.deepEqual(problems("restModule m (\n    status: publshed\n)\n"), [
    "1:error: 'publshed' is not a valid status: expected published, notPublished (or a \"quoted\" ORDS value). Did you mean 'published'?",
  ]);
  assert.deepEqual(problems('restModule m (\n    status: "SOMETHING_NEW"\n)\n'), []);
  assert.deepEqual(problems("restModule m (\n    itemsPerPage: lots\n)\n"), ["1:error: 'itemsPerPage' must be a number"]);
  assert.deepEqual(problems("restModule m (\n    basepath: /m/\n)\n"), [
    "1:warning: 'basepath' is not a known restModule property; it will be passed to ORDS as p_basepath. Did you mean 'basePath'?",
  ]);
  assert.deepEqual(problems("restSchema (\n    urlMapping {\n        tpye: basePath\n    }\n)\n"), [
    "2:error: unknown restSchema urlMapping property 'tpye'. Did you mean 'type'?",
  ]);
  assert.deepEqual(problems("restPrivilege p (\n    roles: [@missing other]\n)\n"), [
    "1:warning: no component named 'missing' in this file",
  ]);
});

test("duplicates", () => {
  const text = 'restModule m (\n\n    template "t" (\n\n        handler GET (\n        )\n\n        handler get (\n        )\n\n    )\n\n)\n';
  assert.deepEqual(problems(text), ["7:error: duplicate handler 'get'"]);
  assert.deepEqual(problems("restModule m (\n)\n\nrestModule m (\n)\n"), ["3:error: duplicate restModule 'm'"]);
  const both = 'restModule m (\n\n    template t (\n\n        handler GET (\n            source {\n                sqlQuery: "a"\n                plsqlCode: "b"\n            }\n        )\n\n    )\n\n)\n';
  assert.deepEqual(problems(both), ["7:error: 'plsqlCode' and 'sqlQuery' both set p_source; keep one"]);
});

test("completion at top level offers top-level components", () => {
  assert.deepEqual(labels("|"), ["restSchema", "restModule", "restRole", "restPrivilege", "restEnabledObject", "plsql"]);
});

test("completion in a component offers unused properties, groups and children", () => {
  const got = labels("restModule m (\n    basePath: /m/\n    |\n)\n");
  assert.deepEqual(got, ["itemsPerPage", "status", "originsAllowed", "comments", "template"]);
  assert.deepEqual(labels("restModule m (\n\n    template t (\n        |\n    )\n\n)\n"), ["priority", "comments", "etag", "handler"]);
});

test("completion in a group offers its properties", () => {
  assert.deepEqual(labels("restModule m (\n\n    template t (\n        etag {\n            |\n        }\n    )\n\n)\n"), ["type", "query"]);
  // still works while the group is unclosed
  assert.deepEqual(labels("restSchema (\n    urlMapping {\n        |\n)\n"), ["type", "pattern"]);
});

test("completion of values", () => {
  assert.deepEqual(labels("restModule m (\n    status: |\n)\n"), ["published", "notPublished"]);
  assert.deepEqual(labels("restSchema (\n    enabled: |\n)\n"), ["true", "false"]);
  const refs = "restRole r1 (\n)\n\nrestModule m1 (\n)\n\nrestPrivilege p (\n    roles: [|\n)\n";
  assert.deepEqual(labels(refs), ["@r1"]);
  assert.deepEqual(labels(refs.replace("roles", "modules")), ["@m1"]);
  assert.deepEqual(labels(refs.replace("roles", "patterns")), []);
});

test("no completion inside code", () => {
  const text = "restModule m (\n\n    template t (\n\n        handler GET (\n            source {\n                sqlQuery:\n                    ```sql\n                    select |\n                    ```\n            }\n        )\n\n    )\n\n)\n";
  const { doc, line, character } = withCursor(text);
  assert.equal(contextAt(doc, line, character).type, "code");
  assert.deepEqual(labels(text), []);
});

test("snippets", () => {
  const { doc, line, character } = withCursor("restModule m (\n    |\n)\n");
  const items = completions(doc, schema, line, character);
  assert.equal(items.find((c) => c.label === "status")!.insertText, "status: ${1|published,notPublished|}");
  assert.equal(items.find((c) => c.label === "template")!.insertText, "template ${1:pattern} (\n\t$0\n)");
});

test("hover shows docs from the grammar", () => {
  const doc = parseApx(example());
  const at = (needle: string) => {
    const line = doc.lines.findIndex((l) => l.includes(needle));
    return hover(doc, schema, line, doc.lines[line].indexOf(needle) + 1);
  };
  assert.match(at("restModule")!.markdown, /Generates `ORDS.DEFINE_MODULE`; the id is `p_module_name`/);
  assert.match(at("basePath:")!.markdown, /ORDS parameter: `p_base_path`/);
  assert.match(at("accessMethod")!.markdown, /Values: `in`, `out`, `inout`/);
  assert.match(at("urlMapping")!.markdown, /Properties: `type`, `pattern`/);
});

test("go to definition follows @references", () => {
  const doc = parseApx(example());
  const line = doc.lines.findIndex((l) => l.includes("@hr.v1"));
  const target = definition(doc, line, doc.lines[line].indexOf("@hr.v1") + 2);
  assert.equal(target?.kind, "restModule");
  assert.equal(target?.id, "hr.v1");
});
