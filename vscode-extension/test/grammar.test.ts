import assert from "node:assert/strict";
import { test } from "node:test";
import { EbnfError, parseEbnf } from "../src/ebnf";
import { buildSchema } from "../src/schema";
import { textMateGrammar } from "../src/syntax";
import { grammar, schema } from "./helpers";

test("every rule reference resolves, apart from character classes", () => {
  assert.deepEqual(
    [...grammar.primitives].sort(),
    ["any-character", "any-character-except-newline", "reference-character", "string-character"],
  );
});

test("EBNF syntax errors report their line", () => {
  assert.throws(() => parseEbnf('<a> ::= "x"\n<b> ::= ( "y"\n'), (e: unknown) => e instanceof EbnfError && /line 2|line 0/.test(e.message));
  assert.throws(() => parseEbnf('<a> ::= "x"\n<a> ::= "y"\n'), /defined twice/);
});

test("component index and nesting come from the grammar", () => {
  assert.deepEqual(schema.topLevel, ["restSchema", "restModule", "restRole", "restPrivilege", "restEnabledObject", "plsql"]);
  assert.deepEqual(schema.components.get("restModule")!.children, ["template"]);
  assert.deepEqual(schema.components.get("template")!.children, ["handler"]);
  assert.deepEqual(schema.components.get("handler")!.children, ["parameter"]);
  assert.equal(schema.components.get("restSchema")!.id, "optional");
  assert.equal(schema.components.get("restModule")!.id, "required");
  assert.equal(schema.components.get("plsql")!.id, "none");
});

test("properties, groups, value types and docs come from the grammar", () => {
  const handler = schema.components.get("handler")!;
  assert.deepEqual([...handler.properties.keys()], ["itemsPerPage", "mimesAllowed", "comments"]);
  assert.deepEqual([...handler.groups.keys()], ["source"]);
  const type = handler.groups.get("source")!.properties.get("type")!;
  assert.equal(type.type, "enum");
  assert.equal(type.param, "p_source_type");
  assert.ok(type.values.includes("queryOneRow"));
  assert.equal(handler.groups.get("source")!.properties.get("sqlQuery")!.type, "code");
  assert.equal(handler.passThrough, true);
  assert.equal(handler.procedure, "ORDS.DEFINE_HANDLER");
  assert.equal(handler.idParam, "p_method");
  assert.match(handler.description, /HTTP method/);

  const priv = schema.components.get("restPrivilege")!;
  assert.equal(priv.properties.get("roles")!.type, "names");
  assert.equal(schema.components.get("restSchema")!.properties.get("enabled")!.type, "boolean");
  assert.equal(schema.components.get("template")!.properties.get("priority")!.type, "number");
  assert.equal(schema.components.get("plsql")!.properties.get("code")!.param, undefined);
  assert.equal(schema.components.get("plsql")!.passThrough, false);
});

test("a new component in the grammar shows up in the schema", () => {
  const extended = GRAMMAR_WITH(`
(* restWidget
   ORDS.DEFINE_WIDGET; id is p_widget
   Test component. *)
<rest-widget> ::= "restWidget" <required-ws> <component-id> <ws> "(" <line-end> { <rest-widget-property-line> } <indent> ")" <line-end>
<rest-widget-property-line> ::= <indent> <rest-widget-property> <line-end>
<rest-widget-property> ::= "size" ":" <ws> ( "small" | "large" | <string> ) (* p_size: How big. *)
`);
  const widget = extended.components.get("restWidget")!;
  assert.ok(extended.topLevel.includes("restWidget"));
  assert.deepEqual(widget.properties.get("size")!.values, ["small", "large"]);
  assert.equal(widget.properties.get("size")!.description, "How big.");
});

function GRAMMAR_WITH(rules: string) {
  const { GRAMMAR_TEXT } = require("./helpers") as typeof import("./helpers");
  const text = GRAMMAR_TEXT.replace("  | <plsql>\n", "  | <plsql>\n  | <rest-widget>\n") + rules;
  return buildSchema(parseEbnf(text));
}

test("TextMate patterns compile and recognise known names", () => {
  const tm = JSON.stringify(textMateGrammar(schema));
  const patterns: string[] = [];
  JSON.parse(tm, (k, v) => {
    if ((k === "match" || k === "begin" || k === "end") && typeof v === "string") patterns.push(v);
    return v;
  });
  for (const p of patterns) assert.doesNotThrow(() => new RegExp(p), p);
  const header = new RegExp(patterns.find((p) => p.includes("restModule"))!);
  assert.match('    template "employees/:id" (', header);
  assert.match("restModule hr.v1 (", header);
  assert.doesNotMatch("restModulex hr.v1 (", header);
});
