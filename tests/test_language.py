import unittest
from pathlib import Path

from ords2apx import apx
from ords2apx.analysis import completions, context_at, definition, format_document, hover, references, validate
from ords2apx.ebnf import EbnfError, parse_ebnf
from ords2apx.schema import bundled_grammar_text, build_schema, default_schema

EXAMPLE = Path(__file__).parent.parent / "examples" / "hr_export.ords.apx"
schema = default_schema()


def problems(text: str) -> list[str]:
    return [f"{p.span.line}:{p.severity}: {p.message}" for p in validate(apx.parse(text), schema)]


def with_cursor(text: str):
    lines = text.split("\n")
    line = next(i for i, l in enumerate(lines) if "|" in l)
    return apx.parse(text.replace("|", "", 1)), line, lines[line].index("|")


def labels(text: str) -> list[str]:
    doc, line, ch = with_cursor(text)
    return [c.label for c in completions(doc, schema, line, ch)]


class GrammarTest(unittest.TestCase):
    def test_references_resolve(self):
        g = parse_ebnf(bundled_grammar_text())
        self.assertEqual(sorted(g.primitives),
                         ["any-character", "any-character-except-newline", "reference-character", "string-character"])

    def test_ebnf_errors(self):
        with self.assertRaisesRegex(EbnfError, "defined twice"):
            parse_ebnf('<a> ::= "x"\n<a> ::= "y"\n')
        with self.assertRaises(EbnfError):
            parse_ebnf('<a> ::= ( "y"\n')

    def test_nesting_and_ids(self):
        self.assertEqual(schema.top_level[:2], ["restSchema", "restModule"])
        self.assertEqual(schema.components["restModule"].children, ["template"])
        self.assertEqual(schema.components["handler"].children, ["parameter"])
        self.assertEqual(schema.components["restSchema"].id, "optional")
        self.assertEqual(schema.components["plsql"].id, "none")

    def test_properties_and_docs(self):
        handler = schema.components["handler"]
        t = handler.property("type", "source")
        self.assertEqual((t.type, t.param), ("enum", "p_source_type"))
        self.assertIn("queryOneRow", t.values)
        self.assertEqual(handler.property("plsqlCode", "source").lang, "plsql")
        self.assertTrue(handler.pass_through)
        self.assertEqual((handler.procedure, handler.id_param), ("ORDS.DEFINE_HANDLER", "p_method"))
        self.assertIn("HTTP method", handler.description)
        self.assertEqual(schema.components["restPrivilege"].properties["roles"].type, "names")

    def test_grammar_extension(self):
        text = bundled_grammar_text().replace("  | <plsql>\n", "  | <plsql>\n  | <rest-widget>\n") + '''
(* restWidget
   ORDS.DEFINE_WIDGET; id is p_widget
   Test component. *)
<rest-widget> ::= "restWidget" <required-ws> <component-id> <ws> "(" <line-end> { <rest-widget-property-line> } <indent> ")" <line-end>
<rest-widget-property-line> ::= <indent> <rest-widget-property> <line-end>
<rest-widget-property> ::= "size" ":" <ws> ( "small" | "large" | <string> ) (* p_size: How big. *)
'''
        s = build_schema(parse_ebnf(text))
        self.assertIn("restWidget", s.top_level)
        self.assertEqual(s.components["restWidget"].properties["size"].values, ("small", "large"))
        # and the converter follows it
        from ords2apx.model import apx_to_sql
        sql = apx_to_sql("restWidget w (\n    size: large\n)\n", schema=s)
        self.assertIn("ORDS.DEFINE_WIDGET(\n      p_widget => 'w',\n      p_size   => 'LARGE');", sql)


class Diagnostics(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(problems(EXAMPLE.read_text()), [])

    def test_structure(self):
        self.assertEqual(problems("restModul m (\n)\n"), ["0:error: unknown component 'restModul'. Did you mean 'restModule'?"])
        self.assertEqual(problems("restModule m (\n\n    handler GET (\n    )\n\n)\n"),
                         ["2:error: 'handler' cannot be nested inside restModule (allowed: template)"])
        self.assertEqual(problems("template t (\n)\n"), ["0:error: 'template' must be nested inside restModule"])
        self.assertEqual(problems("restModule (\n)\n"),
                         ["0:error: restModule needs an id (p_module_name), e.g. restModule name ("])
        self.assertEqual(problems("restModule m (\n    basePath: /m/\n"), ["0:error: missing closing ')'"])
        self.assertEqual(problems("restModule m (\n    oops\n)\n"),
                         ["1:error: 'oops' needs a value ('oops: …') or a body ('oops (' / 'oops {')"])

    def test_properties(self):
        self.assertEqual(problems("restModule m (\n    status: publshed\n)\n"), [
            "1:error: 'publshed' is not a valid status: expected published, notPublished (or a \"quoted\" ORDS value)."
            " Did you mean 'published'?"])
        self.assertEqual(problems('restModule m (\n    status: "SOMETHING_NEW"\n)\n'), [])
        self.assertEqual(problems("restModule m (\n    itemsPerPage: lots\n)\n"), ["1:error: 'itemsPerPage' must be a number"])
        self.assertEqual(problems("restModule m (\n    basepath: /m/\n)\n"), [
            "1:warning: 'basepath' is not a known restModule property; it will be passed to ORDS as p_basepath."
            " Did you mean 'basePath'?"])
        self.assertEqual(problems("restSchema (\n    urlMapping {\n        tpye: basePath\n    }\n)\n"),
                         ["2:error: unknown restSchema urlMapping property 'tpye'. Did you mean 'type'?"])
        self.assertEqual(problems("restModule m1 (\n)\n\nrestPrivilege p (\n    roles: [@missing @m1]\n)\n"), [
            "4:warning: no component named 'missing' in this project",
            "4:warning: 'm1' is a restModule, not a restRole"])

    def test_duplicates(self):
        text = 'restModule m (\n\n    template "t" (\n\n        handler GET (\n        )\n\n        handler get (\n        )\n\n    )\n\n)\n'
        self.assertEqual(problems(text), ["7:error: duplicate handler 'get'"])
        self.assertEqual(problems("restModule m (\n)\n\nrestModule m (\n)\n"), ["3:error: duplicate restModule 'm'"])
        both = ('restModule m (\n\n    template t (\n\n        handler GET (\n            source {\n'
                '                sqlQuery: "a"\n                plsqlCode: "b"\n            }\n        )\n\n    )\n\n)\n')
        self.assertEqual(problems(both), ["7:error: 'plsqlCode' and 'sqlQuery' both set p_source; keep one"])


class Completion(unittest.TestCase):
    def test_top_level(self):
        self.assertEqual(labels("|"), schema.top_level)

    def test_component(self):
        self.assertEqual(labels("restModule m (\n    basePath: /m/\n    |\n)\n"),
                         ["itemsPerPage", "status", "originsAllowed", "comments", "template"])
        self.assertEqual(labels("restModule m (\n\n    template t (\n        |\n    )\n\n)\n"),
                         ["priority", "comments", "etag", "handler"])

    def test_group(self):
        self.assertEqual(labels("restModule m (\n\n    template t (\n        etag {\n            |\n        }\n    )\n\n)\n"),
                         ["type", "query"])
        self.assertEqual(labels("restSchema (\n    urlMapping {\n        |\n)\n"), ["type", "pattern"])

    def test_values(self):
        self.assertEqual(labels("restModule m (\n    status: |\n)\n"), ["published", "notPublished"])
        self.assertEqual(labels("restSchema (\n    enabled: |\n)\n"), ["true", "false"])
        refs = "restRole r1 (\n)\n\nrestModule m1 (\n)\n\nrestPrivilege p (\n    roles: [|\n)\n"
        self.assertEqual(labels(refs), ["@r1"])
        self.assertEqual(labels(refs.replace("roles", "modules")), ["@m1"])
        self.assertEqual(labels(refs.replace("roles", "patterns")), [])

    def test_no_completion_in_code(self):
        text = ("restModule m (\n\n    template t (\n\n        handler GET (\n            source {\n"
                "                sqlQuery:\n                    ```sql\n                    select |\n"
                "                    ```\n            }\n        )\n\n    )\n\n)\n")
        doc, line, ch = with_cursor(text)
        self.assertEqual(context_at(doc, line, ch).type, "code")
        self.assertEqual(labels(text), [])

    def test_snippets(self):
        doc, line, ch = with_cursor("restModule m (\n    |\n)\n")
        items = {c.label: c for c in completions(doc, schema, line, ch)}
        self.assertEqual(items["status"].insert_text, "status: ${1|published,notPublished|}")
        self.assertEqual(items["template"].insert_text, "template ${1:pattern} (\n\t$0\n)")
        doc, line, ch = with_cursor("|")
        plsql = next(c for c in completions(doc, schema, line, ch) if c.label == "plsql")
        self.assertEqual(plsql.insert_text, "plsql (\n\tcode:\n\t\t```plsql\n\t\t$0\n\t\t```\n)")


class Navigation(unittest.TestCase):
    def setUp(self):
        self.doc = apx.parse(EXAMPLE.read_text())

    def at(self, needle: str):
        line = next(i for i, l in enumerate(self.doc.lines) if needle in l)
        return line, self.doc.lines[line].index(needle) + 1

    def test_hover(self):
        self.assertIn("Generates `ORDS.DEFINE_MODULE`; the id is `p_module_name`",
                      hover(self.doc, schema, *self.at("restModule")).markdown)
        self.assertIn("ORDS parameter: `p_base_path`", hover(self.doc, schema, *self.at("basePath:")).markdown)
        self.assertIn("Values: `in`, `out`, `inout`", hover(self.doc, schema, *self.at("accessMethod")).markdown)
        self.assertIn("Properties: `type`, `pattern`", hover(self.doc, schema, *self.at("urlMapping")).markdown)

    def test_definition_and_references(self):
        line, ch = self.at("@hr.v1")
        target = definition(self.doc, line, ch + 1)
        self.assertEqual((target.kind, target.id), ("restModule", "hr.v1"))
        self.assertEqual(len(references(self.doc, *self.at("restModule hr.v1"))), 0)  # cursor on the kind
        line, ch = self.at("hr.v1 (")
        self.assertEqual([s.line for s in references(self.doc, line, ch)], [self.at("@hr.v1")[0]])


class Formatting(unittest.TestCase):
    def test_formats_to_canonical_layout(self):
        self.assertEqual(format_document("restModule m (\n  basePath:   /m/\n\n\n  status: published\n)"),
                         "restModule m (\n    basePath: /m/\n    status: published\n)\n")

    def test_refuses_when_it_could_lose_text(self):
        self.assertIsNone(format_document("restModule m (\n    oops\n)\n"))
        self.assertIsNone(format_document("restModule m (\n    // keep me\n    basePath: /m/\n)\n"))
        self.assertIsNotNone(format_document("// header comment\n\nrestModule m (\n)\n"))


if __name__ == "__main__":
    unittest.main()
