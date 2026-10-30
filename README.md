# ords2apx

Converts ORDS REST export scripts (the `ORDS.DEFINE_MODULE` / `DEFINE_TEMPLATE` /
`DEFINE_HANDLER` PL/SQL that SQL Developer, SQLcl and ORDS write) into
APEXlang-style `.apx` files, and back again.

Diffing and reviewing a REST API in git is hard when it lives in an ORDS
export. The same API in `.apx` reads like this:

```
restModule hr.v1 (
    basePath: /hr/v1/
    itemsPerPage: 25
    status: published

    template "employees/:id" (
        priority: 0
        etag {
            type: hash
        }

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

## Usage

No dependencies; Python 3.10+.

```sh
python3 -m ords2apx export.sql              # -> export.apx
python3 -m ords2apx export.apx              # -> export.sql, ready to run in SQLcl / SQL*Plus
python3 -m ords2apx export.sql -o -         # to stdout
python3 -m ords2apx check export.apx        # validate only
python3 -m unittest discover -s tests -t .  # tests
```

`pip install -e .` also gives you an `ords2apx` command.

## The language

The syntax is Oracle's APEXlang (APEX 26.1). The core layout and value rules are
copied from Oracle's published
[`apexlang.ebnf`](https://github.com/oracle/skills/tree/main/apex/apexlang/26.1),
and the formatting matches Oracle's example applications. APEXlang has no ORDS
components, so this project defines them, following the pattern Oracle uses
for `restDataSource`.

[grammar/ords-apexlang.ebnf](grammar/ords-apexlang.ebnf) is the specification.
The [VS Code extension](vscode-extension/README.md) builds its highlighting,
completion, hovers and validation from it. The Python tests check that the
converter's mapping agrees with it: the same components, properties, ORDS
parameters and enum values.

| ORDS call | Component |
| --- | --- |
| `ORDS.ENABLE_SCHEMA` | `restSchema` |
| `ORDS.DEFINE_MODULE` (+ `SET_MODULE_ORIGINS_ALLOWED`) | `restModule` |
| `ORDS.DEFINE_TEMPLATE` | `template`, nested in a module |
| `ORDS.DEFINE_HANDLER` | `handler`, nested in a template, id is the HTTP method |
| `ORDS.DEFINE_PARAMETER` | `parameter`, nested in a handler |
| `ORDS.CREATE_ROLE` | `restRole` |
| `ORDS.DEFINE_PRIVILEGE` (+ `CREATE_PRIVILEGE_MAPPING`) | `restPrivilege` |
| `ORDS.ENABLE_OBJECT` | `restEnabledObject` |
| anything else | `plsql`, kept verbatim |

Conventions:

- ORDS `UPPER_SNAKE` values become camelCase (`NOT_PUBLISHED` → `notPublished`).
  Handler source types get short names (`json/collection` → `collection`,
  `plsql/block` → `plsql`). A quoted value is passed to ORDS unchanged.
- Code is fenced: `sqlQuery` with a `sql` tag, or `plsqlCode` for PL/SQL handlers.
- `NULL` arguments are omitted. Every ORDS parameter involved defaults to NULL.
- An ORDS parameter the converter doesn't know about becomes a camelCase
  property (`p_new_thing` → `newThing`) and converts back the same way. `check`
  warns about these so typos are caught.
- Statements the converter can't model go into a `plsql` block, kept exactly as
  written. Examples are `OAUTH.CREATE_CLIENT`, positional calls, `IF` blocks, and
  handlers whose module isn't in the file.

## Guarantees and limits

- `.apx → .sql → .apx` is byte-for-byte stable. `.sql → .apx → .sql` makes the
  same ORDS calls with the same arguments (minus NULLs), and handler source text
  is kept exactly, whitespace included. The tests check both directions.
- The generated SQL uses its own layout, not the input's. Comments inside the
  PL/SQL block are dropped. Leading `--` header comments are kept.
- A `/` alone on a line inside handler source ends the block in SQL*Plus. ORDS
  exports have the same problem.
- Source containing ```` ``` ```` cannot be fenced, so it is written as a quoted
  one-line string instead.
