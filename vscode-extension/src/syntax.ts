// Generates the TextMate grammar used for highlighting from the schema, so
// names the EBNF defines are highlighted as known and anything else is not.

import { Schema } from "./schema";

export const SCOPE = "source.apx-ords";
const ID = String.raw`"(?:[^"\\]|\\.)*"|[A-Za-z0-9_][A-Za-z0-9_.\-]*`;

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const oneOf = (words: Iterable<string>) => [...new Set(words)].sort((a, b) => b.length - a.length).map(escape).join("|");

export function textMateGrammar(schema: Schema): object {
  const kinds = [...schema.components.keys()];
  const groups = [...schema.components.values()].flatMap((c) => [...c.groups.keys()]);
  const keys = [...schema.components.values()].flatMap((c) => [
    ...c.properties.keys(),
    ...[...c.groups.values()].flatMap((g) => [...g.properties.keys()]),
  ]);

  const header = (kindPattern: string, kindScope: string) => ({
    match: String.raw`^\s*(${kindPattern})(?:\s+(${ID}))?\s*(\()\s*$`,
    captures: {
      1: { name: kindScope },
      2: { name: "entity.name.type.component-id.apx-ords" },
      3: { name: "punctuation.section.parens.begin.apx-ords" },
    },
  });

  const property = (keyPattern: string, keyScope: string) => ({
    // consume the gap after ":" so every value pattern starts at the value
    begin: String.raw`^\s*(${keyPattern})(:)(?:[ \t]+|$)`,
    end: "$",
    beginCaptures: { 1: { name: keyScope }, 2: { name: "punctuation.separator.key-value.apx-ords" } },
    patterns: [{ include: "#value" }],
  });

  return {
    $schema: "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
    name: "ORDS APEXlang",
    scopeName: SCOPE,
    comment: "Generated from grammar/ords-apexlang.ebnf by `npm run build`. Do not edit.",
    patterns: [
      { include: "#comment" },
      { include: "#fence" },
      header(oneOf(kinds), "keyword.other.component.apx-ords"),
      header("[A-Za-z][A-Za-z0-9]*", "variable.other.unknown-component.apx-ords"),
      {
        match: String.raw`^\s*(${oneOf(groups)})\s*(\{)\s*$`,
        captures: { 1: { name: "storage.type.group.apx-ords" }, 2: { name: "punctuation.section.braces.begin.apx-ords" } },
      },
      {
        match: String.raw`^\s*([A-Za-z][A-Za-z0-9]*)\s*(\{)\s*$`,
        captures: { 1: { name: "variable.other.unknown-group.apx-ords" }, 2: { name: "punctuation.section.braces.begin.apx-ords" } },
      },
      property(oneOf(keys), "support.type.property-name.apx-ords"),
      property("[A-Za-z][A-Za-z0-9]*", "variable.other.property.apx-ords"),
      { match: String.raw`^\s*\)\s*$`, name: "punctuation.section.parens.end.apx-ords" },
      { match: String.raw`^\s*\}\s*$`, name: "punctuation.section.braces.end.apx-ords" },
    ],
    repository: {
      comment: {
        patterns: [
          { match: String.raw`^\s*//.*$`, name: "comment.line.double-slash.apx-ords" },
          { begin: String.raw`^\s*/\*`, end: String.raw`\*/`, name: "comment.block.apx-ords" },
        ],
      },
      fence: {
        patterns: [
          {
            begin: String.raw`^\s*(\x60\x60\x60)(sql|plsql)\s*$`,
            end: String.raw`^\s*(\x60\x60\x60)\s*$`,
            beginCaptures: { 1: { name: "punctuation.definition.fence.apx-ords" }, 2: { name: "entity.name.tag.language.apx-ords" } },
            endCaptures: { 1: { name: "punctuation.definition.fence.apx-ords" } },
            contentName: "meta.embedded.block.sql",
            patterns: [{ include: "source.sql" }],
          },
          {
            begin: String.raw`^\s*(\x60\x60\x60)(\w*)\s*$`,
            end: String.raw`^\s*(\x60\x60\x60)\s*$`,
            beginCaptures: { 1: { name: "punctuation.definition.fence.apx-ords" }, 2: { name: "entity.name.tag.language.apx-ords" } },
            endCaptures: { 1: { name: "punctuation.definition.fence.apx-ords" } },
            contentName: "string.unquoted.block.apx-ords",
          },
        ],
      },
      string: {
        begin: '"',
        end: '"',
        name: "string.quoted.double.apx-ords",
        patterns: [{ match: String.raw`\\(?:["\\/bfnrt]|u[0-9A-Fa-f]{4})`, name: "constant.character.escape.apx-ords" }],
      },
      reference: { match: String.raw`@(?:"(?:[^"\\]|\\.)*"|[^\s\]]+)`, name: "variable.other.constant.reference.apx-ords" },
      value: {
        patterns: [
          { match: String.raw`\s*(true|false)\s*$`, captures: { 1: { name: "constant.language.boolean.apx-ords" } } },
          { match: String.raw`\s*(-?\d+(?:\.\d+)?)\s*$`, captures: { 1: { name: "constant.numeric.apx-ords" } } },
          { match: String.raw`\s*(${oneOf(schema.enumValues)})\s*$`, captures: { 1: { name: "support.constant.enum.apx-ords" } } },
          { include: "#string" },
          { include: "#reference" },
          {
            begin: String.raw`\[`,
            end: String.raw`\]`,
            beginCaptures: { 0: { name: "punctuation.definition.array.begin.apx-ords" } },
            endCaptures: { 0: { name: "punctuation.definition.array.end.apx-ords" } },
            patterns: [
              { include: "#string" },
              { include: "#reference" },
              { match: String.raw`[^\s\]"@]+`, name: "string.unquoted.apx-ords" },
            ],
          },
          { match: String.raw`\s*(\S.*?)\s*$`, captures: { 1: { name: "string.unquoted.apx-ords" } } },
        ],
      },
    },
  };
}
