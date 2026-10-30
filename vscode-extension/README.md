# ORDS APEXlang for VS Code

Write Oracle REST Data Services APIs as APEXlang `.ords.apx` files, next to your APEX 26.1 application.

- **Highlighting**, including SQL, PL/SQL and JavaScript inside handler code.
- **Diagnostics**: unknown components and properties with "did you mean", wrong nesting, bad values, duplicates, unresolved `@references` across every `.ords.apx` file in the workspace.
- **Completion** that follows the cursor: components, unused properties, groups, enum values, `@references`.
- **Hover** with each property's ORDS parameter, allowed values and default.
- **Go to definition**, **find references**, **outline**, **workspace symbols**, **format document**.
- **Commands** (palette and explorer menu):
  - *Convert (ORDS script ⇄ .ords.apx)*
  - *Split ORDS Export into Project Folder* / *Build Deployable ORDS Script from Folder*
  - *Generate APEX REST Data Source* / *Generate OpenAPI Document*
  - *Plan Changes Against Database* / *Deploy to Database* (run in a terminal so you see the plan and confirm)

## Requirements

The language features come from the `ords2apx` language server, a Python package:

```sh
pip install 'ords2apx[all]'
```

If it is installed in a virtualenv, set **ORDS APEXlang › Python Path** (`ordsApexlang.pythonPath`) to that environment's `python`.

## Settings

| Setting | |
| --- | --- |
| `ordsApexlang.pythonPath` | Python with ords2apx installed. Default `python3` (`python` on Windows). |
| `ordsApexlang.grammarPath` | Use a different `ords-apexlang.ebnf`; saving it reloads the server. |
| `ordsApexlang.ordsUrl` | Default schema URL for REST Data Source and OpenAPI generation. |

Database commands read the connection from `ORDS2APX_DSN` in the terminal environment, or prompt for it.

## Developing

```sh
pip install -e '..[dev]'
npm ci
PYTHON=python3 npm test     # regenerates syntaxes/ from the grammar, compiles, tests highlighting with vscode-textmate
npm run package             # -> ords-apexlang-<version>.vsix
```
