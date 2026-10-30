# ORDS APEXlang for VS Code

Editor support for `.apx` files written by [ords2apx](../README.md): ORDS REST
modules in APEXlang syntax.

Everything comes from [`grammar/ords-apexlang.ebnf`](../grammar/ords-apexlang.ebnf).
The extension parses the grammar at startup and builds its component schema
from it, and the build generates the highlighting grammar from the same file.
To add a component or property, edit the EBNF.

- **Highlighting.** Components, groups, property names and enum values the
  grammar defines get their own colours, and anything else is visibly
  different. ` ```sql ` and ` ```plsql ` blocks are highlighted as SQL.
- **Completion.** Suggestions depend on where the cursor is: top-level
  components; then a component's unused properties, groups and child
  components; then a group's properties; then a property's values (enums,
  `true`/`false`, and `@references` to roles and modules in the file).
  Inserted as snippets with tab stops.
- **Diagnostics.** Unknown components, properties and groups, with "Did you
  mean" suggestions; wrong nesting; missing ids; bad enum, number and boolean
  values; duplicates; unresolved `@references`; unclosed blocks. An unknown
  camelCase property is a warning, because the converter passes it to ORDS
  (`fooBar` → `p_foo_bar`).
- **Hover.** Descriptions, ORDS procedure and parameter names, and allowed
  values, all taken from the grammar's comments.
- **Go to definition** on `@references`, **outline**, and **workspace symbols**.

## Developing

```sh
npm install
npm test          # tsc, regenerate syntaxes/ from the EBNF, run unit tests
npm run package   # -> ords-apexlang-0.1.0.vsix
code --install-extension ords-apexlang-0.1.0.vsix
```

To work on the grammar with live feedback, set `ordsApexlang.grammarPath` to
`grammar/ords-apexlang.ebnf` in this repository. Saving the EBNF then reloads
completion, hovers and diagnostics. Highlighting changes need `npm run build`
and a window reload.

The language features in `src/analysis.ts` don't use the `vscode` module, so
they are tested with plain `node:test`. `src/extension.ts` only wires them to
VS Code.

## Note

Oracle's SQL Developer extension also opens `.apx` files, for APEX
applications. If both are installed, choose this one per file with
**Change Language Mode → ORDS APEXlang**, or set `files.associations`.
