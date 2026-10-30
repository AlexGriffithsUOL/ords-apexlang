# Patterns

Patterns to copy. Each passes `ords2apx check` (a test enforces it). The shapes in `assets/examples/hr` (collection, item, query parameter, PL/SQL POST with status and Location headers, DELETE, privilege, role, OAuth client) are also deployed to ORDS 26.3 by the project's database tests.

## Read-only collection and item

```
restModule hr.v1 (
    basePath: /hr/v1/
    itemsPerPage: 50

    template "employees/" (

        handler GET (
            source {
                type: collection
                sqlQuery:
                    ```sql
                    select employee_id, first_name, last_name
                      from employees
                     order by last_name
                    ```
            }
        )

    )

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

`collection` returns `{"items": [...], "hasMore": ..., "limit": ..., "offset": ..., "links": [...]}` and pages by `itemsPerPage`. `item` returns one row as an object and 404 when there is none.

## Optional query filter

Declare the query parameter so it is typed and documented:

```
        handler GET (
            source {
                type: collection
                sqlQuery:
                    ```sql
                    select * from employees where department_id = nvl(:dept, department_id)
                    ```
            }

            parameter dept (
                bindVariable: dept
                source: uri
                type: int
                accessMethod: in
            )

        )
```

## Create with PL/SQL, returning 201 and a Location

```
        handler POST (
            mimesAllowed: application/json
            source {
                type: plsql
                plsqlCode:
                    ```plsql
                    declare
                      l_id employees.employee_id%type;
                    begin
                      insert into employees (first_name, last_name, email)
                      values (:first_name, :last_name, :email)
                      returning employee_id into l_id;
                      :status   := 201;
                      :location := l_id;
                    end;
                    ```
            }

            parameter X-ORDS-STATUS-CODE (
                bindVariable: status
                source: header
                type: int
                accessMethod: out
            )

            parameter Location (
                bindVariable: location
                source: header
                type: string
                accessMethod: out
            )

        )
```

JSON body attributes bind by name (`:first_name`).

## Update and delete on the item template

```
        handler PUT (
            source {
                type: plsql
                plsqlCode:
                    ```plsql
                    begin
                      update employees set email = :email where employee_id = :id;
                      if sql%rowcount = 0 then :status := 404; end if;
                    end;
                    ```
            }

            parameter X-ORDS-STATUS-CODE (
                bindVariable: status
                source: header
                type: int
                accessMethod: out
            )

        )

        handler DELETE (
            source {
                type: plsql
                plsqlCode:
                    ```plsql
                    begin
                      delete from employees where employee_id = :id;
                    end;
                    ```
            }
        )
```

## MLE JavaScript handler (ORDS 24.1.1+)

Needs a database with MLE (Oracle Database 23ai; not the "slim" container images) and `EXECUTE DYNAMIC MLE` for the schema. The `.ords.apx` part below is checked; the request/response API inside the JavaScript is ORDS's, so confirm it against Oracle's MLE handler documentation for your ORDS version. Add `mleEnvName: <environment>` when the code imports MLE modules.

```
        handler GET (
            source {
                type: mleJavascript
                javascriptCode:
                    ```javascript
                    (req, resp) => {
                      const rows = session.execute('select count(*) n from employees').rows;
                      resp.content_type('application/json');
                      resp.status(200);
                      resp.send(JSON.stringify(rows[0]));
                    }
                    ```
            }
        )
```

## Protect a module with OAuth2 client credentials

In `security.ords.apx`:

```
restRole hr_api_role (
)

restPrivilege hr.v1.priv (
    label: HR API
    description: Access to the HR employee API
    roles: [@hr_api_role]
    modules: [@hr.v1]
)

restOauthClient hr_reporting (
    grantType: clientCredentials
    description: Reporting service
    supportEmail: ops@example.com
    privileges: [@hr.v1.priv]
    roles: [@hr_api_role]
)
```

Callers get a token from `<schema URL>/oauth/token`. The client secret is created in the database, never in files. To protect only some paths, use `patterns: ["/hr/v1/admin/*"]` instead of `modules`.

## Accept tokens from an external identity provider

```
restJwtProfile (
    issuer: https://idp.example.com/
    audience: hr-api
    jwkUrl: https://idp.example.com/.well-known/jwks.json
)
```

## Publish a table with AutoREST

```
restEnabledObject DEPARTMENTS (
    type: table
    alias: departments
    enabled: true
    autoRestAuth: true
)
```
