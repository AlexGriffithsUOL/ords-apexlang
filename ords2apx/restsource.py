"""Generate APEX REST Data Sources (Oracle APEXlang) for ORDS modules.

    ords2apx rest-source rest/ --url https://host/ords/hr/ --app my-apex-app/

writes, in APEX 26.1's application layout,

    workspace-components/rest-data-source-servers/<server>.apx   restDataSourceServer
    shared-components/rest-data-sources/<source>.apx            restDataSource, one per resource

so an APEX application can consume the REST API it ships with. A resource is
a collection template and its item templates ("employees/" and
"employees/:id"); each handler becomes an operation, URL binds become URL
pattern parameters, and ORDS header and query parameters become operation
parameters.

The output passes SQLcl `apex validate` (see tests/test_restsource.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import apx
from .apx import Boolean, Component, Document, Reference, Text

SERVER_DIR = "workspace-components/rest-data-source-servers"
SOURCE_DIR = "shared-components/rest-data-sources"

DATA_TYPES = {"STRING": "string", "INT": "numeric", "DOUBLE": "numeric", "LONG": "numeric",
              "TIMESTAMP": "timestamp", "BOOLEAN": "boolean"}
# source types APEX can read as JSON rows
JSON_SOURCES = {"collection", "item", "query", "queryOneRow", "plsql", "mleJavascript"}


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "root"


def _text(c: Component, key: str, group: str | None = None) -> str | None:
    v = c.get(key, group)
    return v.value if isinstance(v, Text) else v.text if isinstance(v, (apx.Number, apx.Code)) else None


@dataclass
class _Resource:
    module: Component
    prefix: str  # path under the module, e.g. "employees/"
    templates: list[tuple[Component, str]] = field(default_factory=list)  # (template, remainder)


def _split_pattern(pattern: str) -> tuple[str, str]:
    """'employees/:id' -> ('employees/', ':id'); 'employees/' -> ('employees/', '')."""
    parts = pattern.split("/")
    i = next((n for n, p in enumerate(parts) if p.startswith(":")), len(parts))
    static = "/".join(parts[:i])
    if i < len(parts) and static:
        static += "/"
    return static, "/".join(parts[i:])


def _resources(modules: list[Component]) -> list[_Resource]:
    out: list[_Resource] = []
    for module in modules:
        by_prefix: dict[str, _Resource] = {}
        for t in (c for c in module.children if c.kind == "template"):
            prefix, rest = _split_pattern(t.id or "")
            res = by_prefix.setdefault(prefix, _Resource(module, prefix))
            res.templates.append((t, rest))
        out += by_prefix.values()
    return out


def _join(*parts: str) -> str:
    path = "/".join(p.strip("/") for p in parts if p.strip("/"))
    return "/" + path + "/" if path else "/"


def _operation(handler: Component, rest: str, ids: set[str]) -> Component | None:
    method = (handler.id or "").upper()
    source_type = _text(handler, "type", "source") or "collection"
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        return None
    if method == "GET":
        db = "fetchSingleRow" if rest or source_type in ("item", "queryOneRow") else "fetchRows"
    else:
        db = {"POST": "insert", "PUT": "update", "DELETE": "delete"}.get(method)
    op_id = slug(f"{method}-{rest}" if rest else method)
    while op_id in ids:
        op_id += "-x"
    ids.add(op_id)
    op = Component("operation", op_id)
    op.set("urlPattern", Text(rest or "."), "operation")
    op.set("httpMethod", Text(method.lower()), "operation")
    if db:
        op.set("databaseOperation", Text(db), "operation")
    return op


def _parameter(pid: str, name: str, ptype: str, data_type: str, direction: str, operation: str | None) -> Component:
    p = Component("parameter", pid)
    p.set("name", Text(name))
    if operation:
        p.set("operation", Reference(operation))
    p.set("type", Text(ptype), "parameter")
    p.set("dataType", Text(data_type), "parameter")
    p.set("direction", Text(direction), "parameter")
    if ptype in ("urlQueryString", "httpHeader") and direction == "in":
        p.set("omitWhenValueIsEmpty", Boolean(True), "parameter")
    return p


def _source(res: _Resource, server_id: str, skipped: list[str]) -> Component | None:
    module_id = res.module.id or ""
    base = _text(res.module, "basePath") or "/"
    name = f"{module_id} {res.prefix or '/'}".strip()
    src = Component("restDataSource", slug(f"{module_id}-{res.prefix}"))
    src.set("name", Text(name))
    src.set("type", Text("ords"), "source")
    src.set("remoteServer", Reference(server_id), "source")
    src.set("urlPathPrefix", Text(_join(base, res.prefix)), "source")

    ops: list[Component] = []
    params: list[Component] = []
    op_ids: set[str] = set()
    param_ids: set[str] = set()
    url_binds: list[str] = []
    for template, rest in res.templates:
        url_binds += [b[1:] for b in rest.split("/") if b.startswith(":") and b[1:] not in url_binds]
        for handler in (c for c in template.children if c.kind == "handler"):
            source_type = _text(handler, "type", "source") or "collection"
            if source_type not in JSON_SOURCES:
                skipped.append(f"{module_id} {template.id} {handler.id}: {source_type} is not JSON")
                continue
            op = _operation(handler, rest, op_ids)
            if op is None:
                skipped.append(f"{module_id} {template.id} {handler.id}: unsupported method")
                continue
            ops.append(op)
            for p in (c for c in handler.children if c.kind == "parameter"):
                source = _text(p, "source") or "uri"
                access = (_text(p, "accessMethod") or "in").lower()
                if p.id in url_binds or source == "response":
                    continue
                ptype = "httpHeader" if source == "header" else "urlQueryString"
                if ptype == "urlQueryString" and access != "in":
                    continue
                pid = slug(p.id or "")
                if pid in param_ids:
                    pid = slug(f"{pid}-{op.id}")
                param_ids.add(pid)
                direction = {"in": "in", "out": "out", "inout": "inOut"}.get(access, "in")
                data_type = DATA_TYPES.get((_text(p, "type") or "STRING").upper(), "string")
                params.append(_parameter(pid, p.id, ptype, data_type, direction, op.id))
    if not ops:
        return None
    src.set("name", Text(name), "dataProfile")
    src.set("format", Text("json"), "dataProfile")
    src.set("containsSingleRow", Boolean(all(o.get("databaseOperation", "operation") != Text("fetchRows") for o in ops)),
            "dataProfile")
    src.set("useRawSelectors", Boolean(False), "dataProfile")
    for b in url_binds:
        pid = slug(b)
        param_ids.add(pid)
        params.insert(0, _parameter(pid, b, "urlPattern", "string", "in", None))
    src.children = ops + params
    return src


def generate(docs: list[Document], url: str, server_id: str | None = None) -> tuple[dict[str, str], list[str]]:
    """Return ({app-relative path: APEXlang text}, [skipped handlers])."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"--url must be an absolute URL, e.g. https://host/ords/hr/ (got {url!r})")
    server_id = server_id or slug(f"{parsed.hostname}-{parsed.path}")
    server = Component("restDataSourceServer", server_id)
    server.set("name", Text(f"ORDS {parsed.netloc}{parsed.path}"))
    server.set("url", Text(url if url.endswith("/") else url + "/"), "endpointUrl")
    server.set("promptOnInstall", Boolean(False), "advanced")

    header = ["Generated by ords2apx rest-source from ORDS modules"]
    files = {f"{SERVER_DIR}/{server_id}.apx": apx.dumps(Document([server], header))}
    skipped: list[str] = []
    modules = [c for d in docs for c in d.components if c.kind == "restModule"]
    for res in _resources(modules):
        src = _source(res, server_id, skipped)
        if src is not None:
            files[f"{SOURCE_DIR}/{src.id}.apx"] = apx.dumps(Document([src], header))
    return files, skipped
