"""TextMate grammar for highlighting, generated from the schema so names the
grammar defines are highlighted as known and anything else is not.

    python -m ords2apx textmate > vscode-extension/syntaxes/ords-apexlang.tmLanguage.json
"""

from __future__ import annotations

import json
import re

from .schema import Schema

SCOPE = "source.ords-apx"
ID = r'"(?:[^"\\]|\\.)*"|[A-Za-z0-9_][A-Za-z0-9_.\-]*'
FENCE_LANGS = {"sql": ("sql|plsql", "meta.embedded.block.sql", "source.sql"),
               "javascript": ("javascript|js", "meta.embedded.block.javascript", "source.js")}


def _one_of(words) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=lambda w: (-len(w), w)))


def _header(kinds: str, scope: str) -> dict:
    return {
        "match": rf"^\s*({kinds})(?:\s+({ID}))?\s*(\()\s*$",
        "captures": {"1": {"name": scope}, "2": {"name": "entity.name.type.component-id.ords-apx"},
                     "3": {"name": "punctuation.section.parens.begin.ords-apx"}},
    }


def _group(names: str, scope: str) -> dict:
    return {"match": rf"^\s*({names})\s*(\{{)\s*$",
            "captures": {"1": {"name": scope}, "2": {"name": "punctuation.section.braces.begin.ords-apx"}}}


def _property(keys: str, scope: str) -> dict:
    return {
        # consume the gap after ":" so every value pattern starts at the value
        "begin": rf"^\s*({keys})(:)(?:[ \t]+|$)",
        "end": "$",
        "beginCaptures": {"1": {"name": scope}, "2": {"name": "punctuation.separator.key-value.ords-apx"}},
        "patterns": [{"include": "#value"}],
    }


def _fence(langs: str, content: str, include: str | None) -> dict:
    rule = {
        "begin": rf"^\s*(```)({langs})\s*$",
        "end": r"^\s*(```)\s*$",
        "beginCaptures": {"1": {"name": "punctuation.definition.fence.ords-apx"},
                          "2": {"name": "entity.name.tag.language.ords-apx"}},
        "endCaptures": {"1": {"name": "punctuation.definition.fence.ords-apx"}},
        "contentName": content,
    }
    if include:
        rule["patterns"] = [{"include": include}]
    return rule


def grammar(schema: Schema) -> dict:
    kinds = list(schema.components)
    groups = [g for c in schema.components.values() for g in c.groups]
    keys = [p.key for c in schema.components.values() for p in c.all_properties()]
    return {
        "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
        "name": "ORDS APEXlang",
        "scopeName": SCOPE,
        "comment": "Generated from ords2apx/grammar/ords-apexlang.ebnf by `python -m ords2apx textmate`. Do not edit.",
        "patterns": [
            {"include": "#comment"},
            {"include": "#fence"},
            _header(_one_of(kinds), "keyword.other.component.ords-apx"),
            _header("[A-Za-z][A-Za-z0-9]*", "variable.other.unknown-component.ords-apx"),
            _group(_one_of(groups), "storage.type.group.ords-apx"),
            _group("[A-Za-z][A-Za-z0-9]*", "variable.other.unknown-group.ords-apx"),
            _property(_one_of(keys), "support.type.property-name.ords-apx"),
            _property("[A-Za-z][A-Za-z0-9]*", "variable.other.property.ords-apx"),
            {"match": r"^\s*\)\s*$", "name": "punctuation.section.parens.end.ords-apx"},
            {"match": r"^\s*\}\s*$", "name": "punctuation.section.braces.end.ords-apx"},
        ],
        "repository": {
            "comment": {"patterns": [
                {"match": r"^\s*//.*$", "name": "comment.line.double-slash.ords-apx"},
                {"begin": r"^\s*/\*", "end": r"\*/", "name": "comment.block.ords-apx"},
            ]},
            "fence": {"patterns": [_fence(langs, content, include) for langs, content, include in FENCE_LANGS.values()]
                      + [_fence(r"\w*", "string.unquoted.block.ords-apx", None)]},
            "string": {
                "begin": '"', "end": '"', "name": "string.quoted.double.ords-apx",
                "patterns": [{"match": r'\\(?:["\\/bfnrt]|u[0-9A-Fa-f]{4})', "name": "constant.character.escape.ords-apx"}],
            },
            "reference": {"match": r'@(?:"(?:[^"\\]|\\.)*"|[^\s\]]+)', "name": "variable.other.constant.reference.ords-apx"},
            "value": {"patterns": [
                {"match": r"\s*(true|false)\s*$", "captures": {"1": {"name": "constant.language.boolean.ords-apx"}}},
                {"match": r"\s*(-?\d+(?:\.\d+)?)\s*$", "captures": {"1": {"name": "constant.numeric.ords-apx"}}},
                {"match": rf"\s*({_one_of(schema.enum_values)})\s*$",
                 "captures": {"1": {"name": "support.constant.enum.ords-apx"}}},
                {"include": "#string"},
                {"include": "#reference"},
                {
                    "begin": r"\[", "end": r"\]",
                    "beginCaptures": {"0": {"name": "punctuation.definition.array.begin.ords-apx"}},
                    "endCaptures": {"0": {"name": "punctuation.definition.array.end.ords-apx"}},
                    "patterns": [{"include": "#string"}, {"include": "#reference"},
                                 {"match": r'[^\s\]"@]+', "name": "string.unquoted.ords-apx"}],
                },
                {"match": r"\s*(\S.*?)\s*$", "captures": {"1": {"name": "string.unquoted.ords-apx"}}},
            ]},
        },
    }


def dumps(schema: Schema) -> str:
    return json.dumps(grammar(schema), indent=2) + "\n"
