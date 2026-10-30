# ORDS APEXlang component reference

Generated from the grammar by `ords2apx reference` (ords2apx 0.2.0). Do not edit by hand.

Components in nesting order:

- `restSchema`
- `restModule`
  - `template`
    - `handler`
      - `parameter`
- `restRole`
- `restPrivilege`
- `restEnabledObject`
- `restOauthClient`
- `restJwtProfile`
- `plsql`

## restSchema

Enables the schema for REST and sets the URL prefix all its modules are published under. The id is the schema name; leave it out for the current schema.

- ORDS procedure: `ORDS.ENABLE_SCHEMA`
- id: optional (`p_schema`)
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `enabled` | `true` \| `false` | `p_enabled` | Whether REST access to the schema is enabled. |
| `autoRestAuth` | `true` \| `false` | `p_auto_rest_auth` | Whether the schema's metadata catalog requires authorization. |

Group `urlMapping { … }`:

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `type` | `basePath` \| `baseUrl` \| `"quoted ORDS value"` | `p_url_mapping_type` | How requests are mapped to the schema: a path segment (basePath) or a whole base URL (baseUrl). |
| `pattern` | text | `p_url_mapping_pattern` | The path segment or base URL, e.g. hr. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restModule

A REST module: a set of URL templates published under one base path. The id is the module name.

- ORDS procedure: `ORDS.DEFINE_MODULE`
- id: required (`p_module_name`)
- top level
- contains: `template`

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `basePath` | text | `p_base_path` | Path of the module under the schema's URL, e.g. /hr/v1/. |
| `itemsPerPage` | number | `p_items_per_page` | Default page size for collection handlers in this module. Default `25`. |
| `status` | `published` \| `notPublished` \| `"quoted ORDS value"` | `p_status` | Whether the module is published. Default `published`. |
| `originsAllowed` | text | `p_origins_allowed` | Comma-separated origins allowed to make cross-origin (CORS) requests. Set with ORDS.SET_MODULE_ORIGINS_ALLOWED. |
| `comments` | text | `p_comments` | Free-text comments. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## template

A URL pattern within a module, e.g. "employees/:id". Bind variables in the pattern (:id) are available to the handlers.

- ORDS procedure: `ORDS.DEFINE_TEMPLATE`
- id: required (`p_pattern`)
- nested in: `restModule`
- contains: `handler`

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `priority` | number | `p_priority` | Evaluation order when more than one pattern matches a URL; 0 (low) to 9 (high). Default `0`. |
| `comments` | text | `p_comments` | Free-text comments. |

Group `etag { … }`:

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `type` | `hash` \| `query` \| `none` \| `"quoted ORDS value"` | `p_etag_type` | How the entity tag is computed: a hash of the response, a query, or none. Default `hash`. |
| `query` | sql code (fenced) | `p_etag_query` | Query whose result is the entity tag, when type is query. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## handler

Serves one HTTP method on a template. The id is the method: GET, POST, PUT or DELETE.

- ORDS procedure: `ORDS.DEFINE_HANDLER`
- id: required (`p_method`)
- nested in: `template`
- contains: `parameter`

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `itemsPerPage` | number | `p_items_per_page` | Page size for collection results; overrides the module's. |
| `mimesAllowed` | text | `p_mimes_allowed` | Comma-separated MIME types the request body may have. |
| `mleEnvName` | text | `p_mle_env_name` | MLE environment that resolves the JavaScript's imports, when the source type is mleJavascript. |
| `comments` | text | `p_comments` | Free-text comments. |

Group `source { … }`:

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `type` | `collection` \| `item` \| `query` \| `queryOneRow` \| `csvQuery` \| `plsql` \| `media` \| `mleJavascript` \| `"quoted ORDS value"` | `p_source_type` | What the handler runs and how the result is returned. collection: json/collection, item: json/item, query: json/query, queryOneRow: json/query;type=single_row, csvQuery: csv/query, plsql: plsql/block, media: resource/lob, mleJavascript: mle/javascript (ORDS 24.1.1+). |
| `sqlQuery` | sql code (fenced) | `p_source` | The SQL query the handler runs. |
| `plsqlCode` | plsql code (fenced) | `p_source` | The PL/SQL block the handler runs, when type is plsql. |
| `javascriptCode` | javascript code (fenced) | `p_source` | The MLE JavaScript the handler runs, when type is mleJavascript. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## parameter

Binds a request header, response header or URI value to a bind variable of the handler. The id is the header or query parameter name.

- ORDS procedure: `ORDS.DEFINE_PARAMETER`
- id: required (`p_name`)
- nested in: `handler`

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `bindVariable` | text | `p_bind_variable_name` | Bind variable in the handler source, without the colon. |
| `source` | `header` \| `response` \| `uri` \| `"quoted ORDS value"` | `p_source_type` | Where the value comes from or goes to. |
| `type` | `string` \| `int` \| `double` \| `boolean` \| `long` \| `timestamp` \| `resultset` \| `"quoted ORDS value"` | `p_param_type` | Data type of the value. |
| `accessMethod` | `in` \| `out` \| `inout` \| `"quoted ORDS value"` | `p_access_method` | Whether the value is read from the request, written to the response, or both. |
| `comments` | text | `p_comments` | Free-text comments. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restRole

A role that privileges can require. The id is the role name.

- ORDS procedure: `ORDS.CREATE_ROLE`
- id: required (`p_role_name`)
- top level

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restPrivilege

Protects modules and URL patterns: callers need one of the roles. The id is the privilege name.

- ORDS procedure: `ORDS.DEFINE_PRIVILEGE`
- id: required (`p_privilege_name`)
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `label` | text | `p_label` | Name shown to users when they are asked to approve access. |
| `description` | text | `p_description` | Description shown to users when they are asked to approve access. |
| `roles` | array of names / `@references` | `p_roles` | Roles that grant this privilege. Refer to a restRole in this file with @name. |
| `modules` | array of names / `@references` | `p_modules` | Modules this privilege protects. Refer to a restModule in this file with @name. |
| `patterns` | array of names / `@references` | `p_patterns` | URL patterns this privilege protects, e.g. "/hr/v1/employees/*". Also set by ORDS.CREATE_PRIVILEGE_MAPPING. |
| `comments` | text | `p_comments` | Free-text comments. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restEnabledObject

Publishes a table, view or PL/SQL object through AutoREST. The id is the object name.

- ORDS procedure: `ORDS.ENABLE_OBJECT`
- id: required (`p_object`)
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `schema` | text | `p_schema` | Schema that owns the object; defaults to the current schema. |
| `type` | `table` \| `view` \| `package` \| `procedure` \| `function` \| `"quoted ORDS value"` | `p_object_type` | Kind of database object. |
| `alias` | text | `p_object_alias` | Name of the object in the URL. |
| `enabled` | `true` \| `false` | `p_enabled` | Whether AutoREST access is enabled. |
| `autoRestAuth` | `true` \| `false` | `p_auto_rest_auth` | Whether access requires authorization. |
| `comments` | text | `p_comments` | Free-text comments. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restOauthClient

An OAuth client that can call protected modules. Deployed with ORDS_SECURITY.IMPORT_CLIENT, which keeps the client id across environments; ORDS_SECURITY.REGISTER_CLIENT and the legacy OAUTH.CREATE_CLIENT are read too. The client secret is never exported. The id is the client name.

- ORDS procedure: `ORDS_SECURITY.IMPORT_CLIENT`
- id: required (`p_name`)
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `grantType` | `clientCredentials` \| `authorizationCode` \| `implicit` \| `"quoted ORDS value"` | `p_grant_type` | OAuth flow the client uses: client_credentials, authorization_code or implicit. |
| `clientId` | text | `p_client_id` | The client id. Keep it to give the client the same id in every environment. Filled in by ORDS when left out. |
| `owner` | text | `p_owner` | Schema that owns the client; the current schema when left out. Filled in by ORDS when left out. |
| `description` | text | `p_description` | Shown to users when they approve access. |
| `supportEmail` | text | `p_support_email` | Contact address for the client. |
| `supportUri` | text | `p_support_uri` | Support page for the client. |
| `privileges` | array of names / `@references` | `p_privilege_names` | Privileges the client can request. Refer to a restPrivilege in this project with @name. |
| `roles` | array of names / `@references` | `p_roles` | Roles granted to the client with ORDS_SECURITY.GRANT_CLIENT_ROLE. Refer to a restRole with @name. |
| `originsAllowed` | text | `p_origins_allowed` | Comma-separated origins allowed to call with the client's tokens. |
| `redirectUri` | text | `p_redirect_uri` | Where users return after approving access (authorizationCode and implicit). |
| `tokenDuration` | number | `p_token_duration` | Access token lifetime in seconds. |
| `refreshDuration` | number | `p_refresh_duration` | Refresh token lifetime in seconds. |
| `codeDuration` | number | `p_code_duration` | Authorization code lifetime in seconds. |
| `tokenEndpointAuthMethod` | text | `p_token_endpoint_auth_method` | How the client authenticates at the token endpoint. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## restJwtProfile

Accepts JSON Web Tokens from an external identity provider for this schema's protected modules. A schema has at most one, so it takes no id.

- ORDS procedure: `ORDS_SECURITY.CREATE_JWT_PROFILE`
- id: none
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `issuer` | text | `p_issuer` | Expected iss claim, e.g. https://idp.example.com/. |
| `audience` | text | `p_audience` | Expected aud claim. |
| `jwkUrl` | text | `p_jwk_url` | URL of the identity provider's JSON Web Key Set. |
| `description` | text | `p_description` | Free-text description. |
| `allowedSkew` | number | `p_allowed_skew` | Clock skew tolerated, in seconds. |
| `allowedAge` | number | `p_allowed_age` | Maximum token age, in seconds, from its iat claim. |
| `roleClaimName` | text | `p_role_claim_name` | Claim that lists the caller's roles. |
| `authorizationServerUrl` | text | `p_authorization_server_url` | Authorization server advertised to clients. |
| `dbAccessTokenEndpoint` | text | `p_db_access_token_endpoint` | Token endpoint for database access tokens. |
| `dbAccessCredentialName` | text | `p_db_access_credential_name` | Database credential used to get database access tokens. |
| `dbAccessScope` | text | `p_db_access_scope` | Scope requested for database access tokens. |

Other camelCase properties are passed to ORDS as parameters (`fooBar` → `p_foo_bar`), with a warning.

## plsql

A statement the converter does not model, such as OAUTH.CREATE_CLIENT, kept verbatim and run in place.

- id: none
- top level

| Property | Value | ORDS parameter | Notes |
| --- | --- | --- | --- |
| `code` | plsql code (fenced) | — | The PL/SQL statement, including its trailing semicolon. |
