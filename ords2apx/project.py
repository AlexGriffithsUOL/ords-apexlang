"""Project layout: one .ords.apx file per module, next to an APEX 26.1 app.

    rest/
      schema.ords.apx           restSchema, restEnabledObject
      modules/<module>.ords.apx one restModule each
      security.ords.apx         restRole, restPrivilege, OAuth clients, JWT profiles
      other.ords.apx            plsql blocks the converter does not model

Any other *.ords.apx files in the folder are part of the project too; the
layout above is only what `split` writes. `build` orders components by kind,
not by file, so references resolve when the script runs.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import apx
from .apx import Component, Document

SUFFIX = ".ords.apx"

# deployment order; kinds not listed go after the listed ones, plsql last
KIND_ORDER = ["restSchema", "restEnabledObject", "restModule", "restRole", "restPrivilege",
              "restJwtProfile", "restOauthClient", "plsql"]
FILE_FOR_KIND = {
    "restSchema": "schema", "restEnabledObject": "schema",
    "restRole": "security", "restPrivilege": "security", "restJwtProfile": "security", "restOauthClient": "security",
}


class ProjectError(ValueError):
    pass


def module_filename(module_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", module_id).strip("._") or "module"


def split(doc: Document, source_name: str | None = None) -> dict[str, Document]:
    """Split one document into project files: {relative path: document}."""
    header = [f"Split from {source_name} by ords2apx" if source_name else "Split by ords2apx"]
    files: dict[str, Document] = {}
    used: set[str] = set()
    for c in doc.components:
        if c.kind == "restModule":
            name = module_filename(c.id or "")
            while name.lower() in used:
                name += "_"
            used.add(name.lower())
            path = f"modules/{name}{SUFFIX}"
        else:
            path = FILE_FOR_KIND.get(c.kind, "other") + SUFFIX
        files.setdefault(path, Document(header=header)).components.append(c)
    return files


def write_split(doc: Document, directory: Path, source_name: str | None = None, force: bool = False) -> list[Path]:
    files = split(doc, source_name)
    if directory.exists() and any(directory.rglob(f"*{SUFFIX}")) and not force:
        raise ProjectError(f"{directory} already has .ords.apx files; use --force to overwrite them")
    written = []
    for rel, part in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(apx.dumps(part), encoding="utf-8")
        written.append(path)
    return written


def project_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob(f"*{SUFFIX}")
                  if not any(part.startswith(".") for part in p.relative_to(directory).parts))


def load(path: Path) -> list[tuple[Path, Document]]:
    """Load a single file or every .ords.apx file under a folder (strict parse)."""
    paths = project_files(path) if path.is_dir() else [path]
    if not paths:
        raise ProjectError(f"no {SUFFIX} files in {path}")
    out = []
    for p in paths:
        try:
            out.append((p, apx.loads(p.read_text(encoding="utf-8"))))
        except apx.ApxSyntaxError as e:
            raise ProjectError(f"{p}: {e}") from None
    return out


def merge(docs: list[Document]) -> Document:
    """All components of a project in deployment order (stable within a kind)."""
    components: list[Component] = [c for d in docs for c in d.components]

    def rank(c: Component) -> int:
        if c.kind == "plsql":
            return len(KIND_ORDER) + 1
        return KIND_ORDER.index(c.kind) if c.kind in KIND_ORDER else len(KIND_ORDER)

    return Document(sorted(components, key=rank))
