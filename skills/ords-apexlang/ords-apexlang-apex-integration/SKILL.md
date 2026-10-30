---
name: ords-apexlang-apex-integration
description: Connect ORDS REST APIs written as .ords.apx to Oracle APEX 26.1 applications - generate APEXlang REST Data Sources and a REST Data Source Server into an APEX app folder, and generate OpenAPI 3 documents for other clients.
---

# Using ORDS APIs from APEX 26.1

## REST Data Sources for an APEX application

1. Find the APEX application folder (it has `application.apx` and `.apex/apexlang.json`) and the ORDS project (`rest/`).
2. Get the ORDS URL of the schema from the user, e.g. `https://host/ords/hr/` (ORDS base URL + the schema's `urlMapping` pattern).
3. Generate: `ords2apx rest-source rest/ --url https://host/ords/hr/ --app path/to/app/`. It writes:
   - `workspace-components/rest-data-source-servers/<server>.apx` — a `restDataSourceServer`
   - `shared-components/rest-data-sources/<source>.apx` — one `restDataSource` (type `ords`) per resource: a collection template and its item templates, with an operation per handler and URL, query and header parameters.
   Handlers that do not return JSON rows (CSV, media) are skipped and listed.
4. Validate the application with Oracle's `apexlang-compile` skill (SQLcl `apex validate -input <app>`). The generated files pass it; if validation fails, report the error rather than editing generated files by hand.
5. Protected modules need credentials in APEX: tell the user to create a Web Credential (OAuth2 client credentials, using a `restOauthClient` from the project and its secret from the database) and set it on the REST Data Source. Do not put secrets in files.

Use `--server-id` to reuse an existing `restDataSourceServer` id. Rerunning overwrites the generated files; changes made to them by hand are lost.

## OpenAPI

`ords2apx openapi rest/ --url https://host/ords/hr/ -o openapi.json` writes an OpenAPI 3.0 document: paths from templates (`:id` → `{id}`), parameters, ORDS response shapes, and OAuth2 client-credentials security for privileged paths (token URL `<schema URL>/oauth/token`). Use it for API docs, client generation or contract tests. It describes rows as free-form objects; add schemas by hand if clients need them.
