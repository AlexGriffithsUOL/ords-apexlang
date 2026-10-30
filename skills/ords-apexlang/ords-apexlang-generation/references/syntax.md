# ORDS APEXlang syntax

The layout is Oracle's APEXlang (APEX 26.1). Line breaks matter; every component header, property, group brace and closing bracket is on its own line.

```
// Comments start with // at the beginning of a line, or /* ... */.

restModule hr.v1 (                  kind, id, then "(" on the same line
    basePath: /hr/v1/               property: value runs to the end of the line
    status: published               enum values are camelCase identifiers

    template "employees/:id" (       ids that are not identifiers are "quoted"
        etag {                      group: name, then "{"
            type: hash
        }

        handler GET (               a blank line before each nested component
            source {
                type: item
                sqlQuery:           code: the key alone, then a fenced block
                    ```sql
                    select * from employees where employee_id = :id
                    ```
            }
        )

    )

)
```

## Values

| Write | Meaning |
| --- | --- |
| `basePath: /hr/v1/` | Text. Unquoted text runs to the end of the line, spaces included. |
| `label: "25"` | Quoted text. Quote text that is empty, has leading or trailing spaces, starts with `"`, `@`, `[` or `//`, or looks like a number or `true`/`false`. JSON escapes apply. |
| `itemsPerPage: 25` | Number. |
| `enabled: true` | Boolean: `true` or `false` only. |
| `status: published` | Enum, from the reference. `status: "SOMETHING_NEW"` passes a quoted value to ORDS unchanged. |
| `roles: [@hr_api_role "SQL Developer"]` | Array, items separated by spaces. Quote items with spaces. |
| `modules: [@hr.v1]` | Reference to a component defined in the project, by id. |
| fenced block | Code. Indent the fence one level more than its key; the content keeps its own indentation relative to the fence. Tags: `sql`, `plsql`, `javascript`. |

## Ids

- An id is an identifier (letters, digits, `_`, `.`, `-`) or a quoted string: `restModule hr.v1 (`, `template "employees/:id" (`.
- A handler's id is its HTTP method: `handler GET (`.
- `restSchema` takes an optional id (the schema; omit it for the current schema). `restJwtProfile` and `plsql` take none.
- Ids are unique per kind and parent.

## Formatting

`ords2apx` writes files in a canonical layout. The VS Code extension's Format Document applies it, and refuses to format a file with errors or comments in component bodies.
