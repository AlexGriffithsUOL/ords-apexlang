import * as fs from "node:fs";
import * as path from "node:path";
import { parseApx } from "../src/apxDocument";
import { parseEbnf } from "../src/ebnf";
import { buildSchema } from "../src/schema";

// tests run from out/test
export const REPO = path.resolve(__dirname, "..", "..", "..");
export const GRAMMAR_TEXT = fs.readFileSync(path.join(REPO, "grammar", "ords-apexlang.ebnf"), "utf8");
export const grammar = parseEbnf(GRAMMAR_TEXT);
export const schema = buildSchema(grammar);
export const example = () => fs.readFileSync(path.join(REPO, "examples", "hr_export.apx"), "utf8");

/** Parse text containing a `|` cursor marker; return the document and cursor position. */
export function withCursor(text: string) {
  const lines = text.split("\n");
  const line = lines.findIndex((l) => l.includes("|"));
  const character = lines[line].indexOf("|");
  return { doc: parseApx(text.replace("|", "")), line, character };
}
