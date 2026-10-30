// Build step: copy the grammar into the extension and generate the TextMate
// grammar from it. Run by `npm run build` after tsc.

import * as fs from "node:fs";
import * as path from "node:path";
import { parseEbnf } from "./ebnf";
import { buildSchema } from "./schema";
import { textMateGrammar } from "./syntax";

const root = path.resolve(__dirname, "..", "..");
const source = path.resolve(root, "..", "grammar", "ords-apexlang.ebnf");
const text = fs.readFileSync(source, "utf8");
const schema = buildSchema(parseEbnf(text));

fs.mkdirSync(path.join(root, "grammar"), { recursive: true });
fs.writeFileSync(path.join(root, "grammar", "ords-apexlang.ebnf"), text);
fs.mkdirSync(path.join(root, "syntaxes"), { recursive: true });
fs.writeFileSync(
  path.join(root, "syntaxes", "ords-apexlang.tmLanguage.json"),
  JSON.stringify(textMateGrammar(schema), null, 2) + "\n",
);
console.log(`grammar: ${schema.components.size} components from ${path.relative(process.cwd(), source)}`);
