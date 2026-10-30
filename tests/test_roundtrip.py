import re
import unittest
from pathlib import Path

from ords2apx import apx, apx_to_sql, sql_to_apx
from ords2apx.apx import ApxSyntaxError
from ords2apx.model import ConversionError, document_to_calls, script_to_document
from ords2apx.plsql import Call, Raw, parse_script

EXAMPLES = Path(__file__).parent.parent / "examples"


def effective_calls(sql: str) -> list:
    """What a script does to ORDS: calls with NULL args dropped, plus raw statements.

    Mapping calls folded into their privilege are normalised the same way the
    converter folds them, so equivalent scripts compare equal.
    """
    doc = script_to_document(parse_script(sql))
    out = []
    for c in document_to_calls(doc, warn=lambda e: None):
        if isinstance(c, str):
            out.append(("raw", c))
        else:
            out.append((c.proc, {k: v for k, v in c.args.items() if v is not None}))
    return out


def wrap(body: str) -> str:
    return "DECLARE\n  l_roles OWA.VC_ARR;\n  l_modules OWA.VC_ARR;\n  l_patterns OWA.VC_ARR;\nBEGIN\n" + body + "\nEND;\n/\n"


MODULE = """
  ORDS.DEFINE_MODULE(p_module_name => 'm', p_base_path => '/m/', p_status => 'PUBLISHED');
  ORDS.DEFINE_TEMPLATE(p_module_name => 'm', p_pattern => 't/:id');
"""


def handler(source_literal: str, source_type: str = "json/item") -> str:
    return wrap(MODULE + f"""
  ORDS.DEFINE_HANDLER(p_module_name => 'm', p_pattern => 't/:id', p_method => 'GET',
      p_source_type => '{source_type}', p_source => {source_literal});""")


class RoundTrip(unittest.TestCase):
    def assert_round_trips(self, sql: str) -> str:
        text = sql_to_apx(sql)
        back = apx_to_sql(text)
        self.assertEqual(effective_calls(sql), effective_calls(back))
        self.assertEqual(sql_to_apx(back).splitlines()[1:], text.splitlines()[1:])
        return text

    def test_example_export(self):
        self.assert_round_trips((EXAMPLES / "hr_export.sql").read_text())

    def test_source_text_is_preserved_exactly(self):
        source = "select 'a''b' x,\n\t  q from dual\n   \n\nwhere 1 = 1  \n"
        text = self.assert_round_trips(handler("'" + source.replace("'", "''") + "'"))
        handler_call = [c for c in parse_script(apx_to_sql(text)).statements if c.name == "ORDS.DEFINE_HANDLER"][0]
        self.assertEqual(handler_call.args["p_source"], source)

    def test_q_quote_and_concatenation(self):
        self.assert_round_trips(handler("q'[select 'x' from dual]' || chr(10) || 'order by 1'"))

    def test_source_containing_fence_falls_back_to_string(self):
        text = self.assert_round_trips(handler("'select ''```'' from dual'"))
        self.assertIn(r'sqlQuery: "select ', text)

    def test_ords_constants(self):
        sql = handler("'select 1 from dual'", "x").replace("'x'", "ORDS.source_type_collection_feed")
        text = self.assert_round_trips(sql)
        self.assertIn("type: collection", text)

    def test_unknown_parameter_passes_through(self):
        sql = wrap(MODULE.replace("p_status => 'PUBLISHED'", "p_status => 'PUBLISHED', p_new_thing => '42'"))
        text = self.assert_round_trips(sql)
        self.assertIn('newThing: "42"', text)

    def test_unrecognised_statements_are_kept_verbatim(self):
        sql = wrap(MODULE + """
  IF 1 = 1 THEN
    ORDS.DEFINE_HANDLER(p_module_name => 'm', p_pattern => 't/:id', p_method => 'PUT');
  END IF;
  ORDS.DEFINE_SERVICE('svc', 'svc/');
  ORDS.DEFINE_HANDLER(p_module_name => 'nope', p_pattern => 'x', p_method => 'GET');""")
        text = self.assert_round_trips(sql)
        self.assertEqual(text.count("plsql ("), 3)

    def test_privilege_mappings_fold_into_privilege(self):
        sql = wrap(MODULE + """
  ORDS.CREATE_ROLE(p_role_name => 'r');
  l_roles(1) := 'r';
  ORDS.DEFINE_PRIVILEGE(p_privilege_name => 'p', p_roles => l_roles, p_patterns => l_patterns, p_modules => l_modules);
  l_roles.DELETE;
  ORDS.CREATE_PRIVILEGE_MAPPING(p_privilege_name => 'p', p_pattern => '/m/*');""")
        text = sql_to_apx(sql)
        self.assertIn('patterns: ["/m/*"]', text)
        self.assertIn("roles: [r]", text)
        self.assert_round_trips(apx_to_sql(text))

    def test_output_is_stable(self):
        text = sql_to_apx((EXAMPLES / "hr_export.sql").read_text())
        self.assertEqual(apx.dumps(apx.loads(text)), text)


class Grammar(unittest.TestCase):
    """grammar/ords-apexlang.ebnf is the specification; the converter must agree with it."""

    def ebnf_components(self) -> dict:
        text = (EXAMPLES.parent / "grammar" / "ords-apexlang.ebnf").read_text()
        rule_kind = dict(re.findall(r'^<([\w-]+)> ::= "(\w+)" .*"\("', text, re.M))
        components = {}
        for rule, kind in rule_kind.items():
            props = {}
            # property rules of this component and its groups
            for m in re.finditer(rf'^<{rule}(?:-[\w-]+)?-property> ::= (.*?)(?=^<|\Z)', text, re.M | re.S):
                for alt in re.findall(r'"(\w+)" ":" <ws> (.*?) \(\* (-|p_\w+):', m.group(1)):
                    key, value, param = alt
                    values = tuple(re.findall(r'"(\w+)"', value))
                    props[key] = (param, values)
            proc = re.search(rf'\(\* {kind}\n\s+([\w.-]+);', text).group(1)
            components[kind] = (proc, props)
        return components

    def test_converter_matches_grammar(self):
        from ords2apx.model import RAW_KIND, SPECS
        grammar = self.ebnf_components()
        expected = {}
        for spec in SPECS:
            props = {p.key: (p.param, p.values) for p in spec.props}
            if spec.kind == "handler":
                props["plsqlCode"] = ("p_source", ())
            expected[spec.kind] = (spec.proc, props)
        expected[RAW_KIND] = ("-", {"code": ("-", ())})
        self.assertEqual(grammar, expected)


class Errors(unittest.TestCase):
    def test_syntax_error_reports_line(self):
        with self.assertRaises(ApxSyntaxError) as e:
            apx.loads("restModule m (\n    basePath: /m/\n    oops\n)\n")
        self.assertIn("line 3", str(e.exception))

    def test_wrong_nesting(self):
        with self.assertRaises(ConversionError) as e:
            apx_to_sql("restModule m (\n\n    handler GET (\n    )\n\n)\n")
        self.assertIn("cannot be nested inside restModule", str(e.exception))

    def test_bad_types(self):
        with self.assertRaises(ConversionError):
            apx_to_sql("restModule m (\n    itemsPerPage: lots\n)\n")
        with self.assertRaises(ConversionError):
            apx_to_sql("restSchema (\n    enabled: yes\n)\n")

    def test_unknown_group_property(self):
        with self.assertRaises(ConversionError) as e:
            apx_to_sql("restSchema (\n    urlMapping {\n        tpye: basePath\n    }\n)\n")
        self.assertIn("urlMapping.tpye", str(e.exception))

    def test_unknown_property_warns(self):
        warnings = []
        document_to_calls(apx.loads("restModule m (\n    basepath: /m/\n)\n"), warnings.append)
        self.assertIn("p_basepath", str(warnings[0]))


if __name__ == "__main__":
    unittest.main()
