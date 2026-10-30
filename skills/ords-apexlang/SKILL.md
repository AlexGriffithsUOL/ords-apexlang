---
name: ords-apexlang
description: Skills for Oracle REST Data Services (ORDS) modules written as APEXlang (.ords.apx files) with the ords2apx tool, alongside Oracle APEX 26.1 applications. Write REST APIs, convert ORDS export scripts, deploy to a database, and consume the APIs from APEX.
---

# ORDS APEXlang Skills

ORDS APEXlang describes ORDS REST APIs (modules, templates, handlers, parameters, roles, privileges, OAuth clients, JWT profiles, AutoREST objects) in Oracle's APEXlang syntax, in `.ords.apx` files. The `ords2apx` tool validates them, converts them to and from ORDS PL/SQL scripts, and deploys them.

Install the tool once: `pip install 'ords2apx[db]'` (or `pipx install 'ords2apx[db]'`). Check it with `ords2apx --version`. Every command below also works as `python -m ords2apx …`.

## Skills

| Skill | Use it to |
| --- | --- |
| `ords-apexlang-generation/` | Write or change REST APIs as `.ords.apx` files, with the component reference and patterns. |
| `ords-apexlang-migration/` | Turn an existing ORDS export script, or a live schema, into an `.ords.apx` project. |
| `ords-apexlang-deploy/` | Plan and deploy a project to a database, with an approval step. |
| `ords-apexlang-apex-integration/` | Generate APEX REST Data Sources and OpenAPI documents from the APIs. |

## Choosing a Skill

- **New or changed REST API:** `ords-apexlang-generation`. Validate with `ords2apx check` before anything else.
- **The API exists only in the database or in an export script:** `ords-apexlang-migration` first, then edit with `ords-apexlang-generation`.
- **Putting changes into a database:** `ords-apexlang-deploy`. Never deploy without showing the plan to the user and getting approval.
- **An APEX 26.1 app should call the API:** `ords-apexlang-apex-integration`, then validate the app with Oracle's `apexlang-compile` skill.

## Project Layout

A project is a folder of `.ords.apx` files, usually `rest/` next to an APEX application export:

```
rest/
  schema.ords.apx           restSchema, restEnabledObject
  modules/<module>.ords.apx one restModule per file
  security.ords.apx         restRole, restPrivilege, restOauthClient, restJwtProfile
  other.ords.apx            plsql blocks for anything not modelled
```

Any `*.ords.apx` file in the folder belongs to the project. `@references` resolve across files.
