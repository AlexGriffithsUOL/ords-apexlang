---
name: ords-apexlang-deploy
description: Deploy an .ords.apx project to an Oracle database with ORDS - show the plan of added, changed and removed REST modules, privileges and clients, get the user's approval, then apply only the differences with ords2apx deploy.
---

# Deploying an .ords.apx project

Deploying changes a database. Always plan first, show the plan, and get explicit approval for that plan and that database.

## Steps

1. Validate: `ords2apx check rest/`. Stop on errors.
2. Connection: `user/password@host:port/service` from the user, or `ORDS2APX_DSN` already set. Never write the password into a file or echo it. Confirm which database it is (dev, test, production) before going on.
3. Plan: `ords2apx plan rest/`. Show the user the full output:
   - `+` added, `~` changed (with a diff), `-` in the database but not in the project.
   - Without `--prune`, `-` lines are reported and left alone.
4. Ask for approval, naming the database and the counts ("3 to add, 1 to change on DEV"). If the plan shows changes the user did not ask for, stop and report them instead.
5. Deploy: `ords2apx deploy rest/ --yes`. Use `--yes` only after that approval. All changes run in one transaction; on an error nothing is applied and the error is printed.
6. Confirm: `ords2apx plan rest/` must now say "No changes. The database matches the project."

## Removing things

`--prune` removes modules, roles, privileges, OAuth clients and JWT profiles the project no longer defines. Use it only when the user asks for removal and has seen the `-` lines of `ords2apx plan rest/ --prune`. Removing an OAuth client invalidates its secret; removing a module takes its endpoints offline.

ORDS's own AutoREST and SODA roles and privileges are never pruned, and `plsql` blocks found in the database are never removed.

## Without a direct connection

`ords2apx deploy rest/ --sql deploy.sql` writes the deployment script instead of running it (it still connects to compute the plan). For a full install into an empty schema with no connection at all, use `ords2apx build rest/ -o install.sql` and run it with SQLcl: `sql user@db @install.sql`.

## In CI

`ords2apx plan rest/ --detailed-exitcode` exits 0 when the database matches, 2 when a deploy would change something, 1 on errors. Use it to fail a pipeline on drift.
