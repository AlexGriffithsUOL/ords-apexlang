---
name: ords-apexlang-migration
description: Turn existing Oracle REST Data Services (ORDS) definitions into an .ords.apx project - from an ORDS export script (SQL Developer, SQLcl rest export, ORDS_EXPORT) or straight from a database schema with ords2apx pull.
---

# Migrating ORDS definitions to .ords.apx

## From an export script

1. Split the script into a project: `ords2apx split export.sql -d rest/`. Use `--force` only if the user agrees to overwrite existing `.ords.apx` files there.
2. Validate: `ords2apx check rest/`. It must report 0 errors.
3. Review `rest/other.ords.apx` if it exists. It holds `plsql` blocks: statements the converter keeps verbatim because no component models them (positional calls, IF blocks, handlers whose module is not in the script). Tell the user what is in it; offer to rewrite a block as components when a component exists for it.
4. To prove nothing was lost, rebuild and compare: `ords2apx build rest/ -o rebuilt.sql`, then `ords2apx rebuilt.sql -o - ` and `ords2apx export.sql -o -` should describe the same components.

A single file instead of a project: `ords2apx export.sql` writes `export.ords.apx`; `ords2apx export.ords.apx` writes the script back.

## From a database

1. Get a connection from the user as `user/password@host:port/service`, or ask them to set `ORDS2APX_DSN`. Never write the password into a file, and never echo it.
2. `ords2apx pull -d rest/`. This exports the connected schema with ORDS_EXPORT, leaves out what ORDS maintains itself (AutoREST and SODA privileges and roles), and splits the result.
3. `ords2apx check rest/`, then `ords2apx plan rest/` — it must report "No changes". If it does not, report the differences to the user; do not "fix" them by deploying.

## What converts

| ORDS call | Component |
| --- | --- |
| `ORDS.ENABLE_SCHEMA` | `restSchema` |
| `ORDS.DEFINE_MODULE` (+ `SET_MODULE_ORIGINS_ALLOWED`) | `restModule` |
| `ORDS.DEFINE_TEMPLATE` / `DEFINE_HANDLER` / `DEFINE_PARAMETER` | `template` / `handler` / `parameter` |
| `ORDS.DEFINE_SERVICE` | `restModule` with one template and handler |
| `ORDS.CREATE_ROLE` | `restRole` |
| `ORDS.DEFINE_PRIVILEGE` (+ `CREATE_PRIVILEGE_MAPPING`) | `restPrivilege` |
| `ORDS.ENABLE_OBJECT` | `restEnabledObject` |
| `ORDS_SECURITY.IMPORT_CLIENT` / `REGISTER_CLIENT`, `OAUTH.CREATE_CLIENT` (+ `GRANT_CLIENT_ROLE`) | `restOauthClient` |
| `ORDS_SECURITY.CREATE_JWT_PROFILE`, `OAUTH.CREATE_JWT_PROFILE` | `restJwtProfile` |
| anything else | `plsql`, verbatim |

Handler source text is kept exactly, whitespace included. Leading `--` comments of the script are kept as the file header; other comments inside the PL/SQL block are not.
