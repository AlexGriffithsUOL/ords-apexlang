---
name: ords-apexlang-generation
description: Write or change Oracle REST Data Services (ORDS) REST APIs as APEXlang .ords.apx files - modules, URL templates, handlers with SQL, PL/SQL or MLE JavaScript, parameters, roles, privileges, OAuth clients - and validate them with ords2apx check.
---

# Writing ORDS APEXlang

Use this skill to create or edit `.ords.apx` files. Read `references/syntax.md` once, then look up components in `references/components.md` (generated from the grammar, so it is exact) and copy from `references/patterns.md` and `assets/examples/`.

## Workflow

1. **Find the project.** Look for a folder of `*.ords.apx` files (often `rest/`). If there is none and the API already exists in a database or export script, use `ords-apexlang-migration` first. For a new API, create `rest/` with the layout in the parent `SKILL.md`.
2. **Read what is there.** Open the module file you will change and `security.ords.apx`. Keep existing ids: renaming a module, template pattern or handler method creates a new one and leaves the old one in the database.
3. **Design the URLs.** One module per API version or area (`hr.v1`, base path `/hr/v1/`). A collection template ends in `/` (`employees/`); an item template adds a bind (`employees/:id`). One handler per HTTP method.
4. **Write the files.** Follow `references/syntax.md` exactly: one property per line, 4-space indentation, fenced code with a language tag, a blank line before each nested component.
5. **Validate:** `ords2apx check rest/`. Fix every error. Treat each warning as a likely typo: a warning about an unknown property means it will be passed to ORDS as a raw parameter. Repeat until it reports 0 errors and 0 warnings.
6. **Show the result.** Summarise the endpoints added or changed (method + full path). Deployment is a separate step with its own approval (`ords-apexlang-deploy`).

## Rules

- Put SQL in `sqlQuery` (types `collection`, `item`, `query`, `queryOneRow`, `csvQuery`), PL/SQL in `plsqlCode` (type `plsql`), JavaScript in `javascriptCode` (type `mleJavascript`, plus `mleEnvName`). Never two of them in one handler.
- Bind variables in handler code use `:name` and match a URL bind (`:id` in the pattern), a query parameter, or a `parameter` component's `bindVariable`.
- In PL/SQL handlers, set the HTTP status with an OUT `parameter` named `X-ORDS-STATUS-CODE` bound to e.g. `status`, and `:status := 201;`.
- Protect a module by naming it in a `restPrivilege`'s `modules: [@module-id]` and listing `roles`. Use `@references` for components defined in the project.
- Leave out properties that equal their default (see the reference); never invent values for `clientId` or `owner`, which ORDS fills in.
- Never put secrets in `.ords.apx` files. OAuth client secrets are not part of the format.
- Use a `plsql` block only for things no component models, and say so to the user.

## When `check` reports something you cannot fix

The grammar is the authority. Run `ords2apx reference` for the current component list. If a needed ORDS parameter has no property, a camelCase property passes it through (`fooBar` → `p_foo_bar`); tell the user it is unchecked.
