# Changelog

## 0.2.0

- Files are now `.ords.apx`, so they never clash with APEX application `.apx` files or Oracle's SQL Developer extension.
- The grammar (`ords2apx/grammar/ords-apexlang.ebnf`) drives everything: the converter's ORDS mapping, defaults, validation, completion, hovers, highlighting and the component reference.
- New components: `restOauthClient` (`ORDS_SECURITY.IMPORT_CLIENT`/`REGISTER_CLIENT`, `GRANT_CLIENT_ROLE`, legacy `OAUTH.*`) and `restJwtProfile`.
- MLE JavaScript handlers (`type: mleJavascript`, `javascriptCode`, `mleEnvName`).
- `ORDS.DEFINE_SERVICE` converts to a module, template and handler.
- Real ORDS exports convert completely: the trailing `EXCEPTION … RAISE` section is ignored.
- Project folders: `split`, `build`, and `check` across files, with `@references` resolving between files.
- Database commands: `pull`, `plan` (with `--prune` and `--detailed-exitcode`) and `deploy`, tested against ORDS 26.3.
- `rest-source`: APEX REST Data Sources in APEXlang, validated with SQLcl `apex validate`.
- `openapi`: OpenAPI 3.0 documents with OAuth2 security from privileges.
- `lsp`: a Python language server. The VS Code extension is now a thin client of it, with conversion, generation, plan and deploy commands.
- Skills for coding agents in `skills/ords-apexlang`.

## 0.1.0

- First version: convert ORDS export scripts to APEXlang-style files and back.
