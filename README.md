# ords2apx

**Oracle REST Data Services modules as APEXlang.** Keep your ORDS REST APIs in
readable `.ords.apx` files next to your APEX 26.1 application, review them in
pull requests, and deploy only what changed.

```
restModule hr.v1 (
    basePath: /hr/v1/

    template "employees/:id" (

        handler GET (
            source {
                type: item
                sqlQuery:
                    ```sql
                    select * from employees where employee_id = :id
                    ```
            }
        )

    )

)
```

- **Convert** ORDS export scripts (SQL Developer, SQLcl `rest export`, `ORDS_EXPORT`) to `.ords.apx` and back. Handler code is kept exactly.
- **Pull, plan, deploy** against a live database. `plan` shows what would change; `deploy` applies only that.
- **Generate** APEX REST Data Sources (APEXlang, validated by SQLcl `apex validate`) and OpenAPI 3 documents.
- **Edit** with a language server: diagnostics, completion, hover, go to definition, formatting. A VS Code extension is included.
- **AI-ready**: skills for coding agents in [`skills/ords-apexlang`](skills/ords-apexlang/SKILL.md), alongside Oracle's own APEXlang skills.

Tested against ORDS 26.3 on Oracle Database 23ai Free.

## Install

```sh
pipx install 'ords2apx[all]'        # or: pip install 'ords2apx[all]'
```

Python 3.10+. The core needs nothing else; the extras add the language server (`[lsp]`, pygls) and database commands (`[db]`, python-oracledb in thin mode — no Oracle Client needed).

## Quick start

```sh
# an existing export -> a project folder, one file per module
ords2apx split hr_export.sql -d rest/
ords2apx check rest/

# or straight from the database
export ORDS2APX_DSN='hr@db.example.com:1521/freepdb1'   # password is prompted for
ords2apx pull -d rest/

# edit rest/modules/*.ords.apx, then
ords2apx plan rest/
ords2apx deploy rest/
```

## Commands

| Command | |
| --- | --- |
| `ords2apx FILE` | Convert an ORDS export (`.sql`) to `.ords.apx`, or back. |
| `ords2apx check PATH…` | Validate files or project folders (`--strict` fails on warnings). |
| `ords2apx split EXPORT -d DIR` | Export → project folder. |
| `ords2apx build DIR -o install.sql` | Project → one script for SQLcl / SQL*Plus. |
| `ords2apx pull -d DIR` | Database → project folder. ORDS's own AutoREST/SODA objects are left out. |
| `ords2apx plan DIR` | Compare with the database (`--prune` to include removals, `--detailed-exitcode` for CI). |
| `ords2apx deploy DIR` | Apply the plan in one transaction, after confirmation (`--yes`, `--prune`, `--sql FILE`). |
| `ords2apx rest-source PATH --url URL --app APP` | APEX REST Data Sources and a REST Data Source Server, written into an APEX app folder. |
| `ords2apx openapi PATH --url URL` | OpenAPI 3.0 document. |
| `ords2apx lsp` | Language server over stdio, for any LSP editor. |
| `ords2apx reference` | Markdown reference of every component and property. |

## Project layout

```
my-app/                      an APEX 26.1 application (APEXlang export)
  application.apx
  pages/ …
rest/
  schema.ords.apx            restSchema, restEnabledObject
  modules/hr.v1.ords.apx     one restModule per file
  security.ords.apx          restRole, restPrivilege, restOauthClient, restJwtProfile
  other.ords.apx             plsql blocks for anything not modelled
```

`build` and `deploy` order components so references resolve: schema, AutoREST objects, modules, roles, privileges, JWT profile, OAuth clients, then `plsql` blocks.

## The language

The syntax is Oracle's APEXlang (APEX 26.1): its core grammar rules are taken from Oracle's published [`apexlang.ebnf`](https://github.com/oracle/skills/tree/main/apex/apexlang/26.1). APEXlang has no ORDS components, so this project defines them in [`ords2apx/grammar/ords-apexlang.ebnf`](ords2apx/grammar/ords-apexlang.ebnf), following the pattern Oracle uses for `restDataSource`.

That grammar is the single source of truth. The converter's mapping (each component's ORDS procedure, each property's parameter, defaults), validation, completion, hovers, highlighting, and the [component reference](skills/ords-apexlang/ords-apexlang-generation/references/components.md) are all read from it. Adding a property to the grammar is enough for every tool to support it.

| ORDS | Component |
| --- | --- |
| `ORDS.ENABLE_SCHEMA` | `restSchema` |
| `ORDS.DEFINE_MODULE`, `SET_MODULE_ORIGINS_ALLOWED`, `DEFINE_SERVICE` | `restModule` |
| `ORDS.DEFINE_TEMPLATE` / `DEFINE_HANDLER` / `DEFINE_PARAMETER` | `template` / `handler` / `parameter` |
| `ORDS.CREATE_ROLE` | `restRole` |
| `ORDS.DEFINE_PRIVILEGE`, `CREATE_PRIVILEGE_MAPPING` | `restPrivilege` |
| `ORDS.ENABLE_OBJECT` | `restEnabledObject` |
| `ORDS_SECURITY.IMPORT_CLIENT` / `REGISTER_CLIENT`, `GRANT_CLIENT_ROLE` (and legacy `OAUTH.*`) | `restOauthClient` |
| `ORDS_SECURITY.CREATE_JWT_PROFILE` | `restJwtProfile` |
| anything else | `plsql`, kept verbatim |

Handler sources can be SQL (`sqlQuery`), PL/SQL (`plsqlCode`) or MLE JavaScript (`javascriptCode`, ORDS 24.1.1+).

## Editors

- **VS Code:** install the *ORDS APEXlang* extension (or the `.vsix` from a release). It runs `python3 -m ords2apx lsp`; set `ordsApexlang.pythonPath` if ords2apx lives in a virtualenv. See [vscode-extension/README.md](vscode-extension/README.md).
- **Other editors:** point your LSP client at `ords2apx lsp` for `*.ords.apx` files.

Files are `.ords.apx` rather than `.apx`, so they never clash with APEX application files or Oracle's SQL Developer extension.

## Guarantees and limits

- `.ords.apx → .sql → .ords.apx` is byte-for-byte stable. `.sql → .ords.apx → .sql` makes the same ORDS calls with the same arguments; NULL arguments are dropped, and every parameter involved defaults to NULL.
- Deploying a project and then planning reports no changes, and a pulled project matches its database (checked against ORDS 26.3 in CI).
- `plan` ignores differences between a left-out property and its ORDS default, and values ORDS generates (such as an OAuth client's id) that the project leaves out.
- A changed module is redefined as a whole (`ORDS.DEFINE_MODULE` replaces its templates). A changed OAuth client is deleted and re-imported with the same client id, which invalidates its secret.
- OAuth client secrets are never part of the files.
- Comments inside the PL/SQL of an export are not kept; leading `--` comments are. A `/` alone on a line inside handler source ends the block in SQL*Plus (as in ORDS's own exports).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -t .

# database tests: a throwaway Oracle Free + ORDS in Docker
scripts/test-db.sh start
eval "$(scripts/test-db.sh env | sed 's/^/export /')"
.venv/bin/python -m unittest discover -s tests -t .
scripts/test-db.sh stop

# the VS Code extension
cd vscode-extension && npm ci && PYTHON=$PWD/../.venv/bin/python npm test
```

The REST Data Source test also runs SQLcl `apex validate` when SQLcl is on the PATH (or `SQLCL_BIN` is set).

Releases: bump the version in `pyproject.toml`, `ords2apx/__init__.py` and `vscode-extension/package.json`, update [CHANGELOG.md](CHANGELOG.md), and push a `vX.Y.Z` tag. [`.github/workflows/release.yml`](.github/workflows/release.yml) publishes to PyPI, the VS Code Marketplace and Open VSX.

## License

[UPL-1.0](LICENSE). Includes material from Oracle under the same license; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Not affiliated with Oracle.
