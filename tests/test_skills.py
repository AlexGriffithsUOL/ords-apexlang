"""The skills must stay correct: generated reference current, examples and patterns valid."""

import re
import textwrap
import unittest
from pathlib import Path

from ords2apx import apx
from ords2apx.analysis import validate
from ords2apx.project import project_files
from ords2apx.reference import generate
from ords2apx.schema import default_schema

SKILLS = Path(__file__).parent.parent / "skills" / "ords-apexlang"
GENERATION = SKILLS / "ords-apexlang-generation"
schema = default_schema()


def problems(text: str, project: dict | None = None) -> list[str]:
    return [f"{p.span.line + 1}: {p.severity}: {p.message}" for p in validate(apx.parse(text), schema, project)]


class Skills(unittest.TestCase):
    def test_every_skill_has_front_matter(self):
        for skill in SKILLS.rglob("SKILL.md"):
            head = skill.read_text().split("---")[1]
            self.assertRegex(head, r"\nname: [a-z0-9-]+\n", skill)
            self.assertRegex(head, r"\ndescription: .{40,}", skill)
            self.assertEqual(re.search(r"name: (\S+)", head).group(1), skill.parent.name, skill)

    def test_component_reference_is_current(self):
        current = (GENERATION / "references" / "components.md").read_text()
        self.assertEqual(current, generate(schema),
                         "run: ords2apx reference > skills/ords-apexlang/ords-apexlang-generation/references/components.md")

    def test_example_project_is_valid(self):
        files = project_files(GENERATION / "assets" / "examples")
        self.assertTrue(files)
        docs = {p: apx.parse(p.read_text()) for p in files}
        targets = {c.id: c for d in docs.values() for c in d.components if c.id is not None}
        for path, doc in docs.items():
            self.assertEqual([p.message for p in validate(doc, schema, targets)], [], path)

    def test_patterns_are_valid(self):
        text = (GENERATION / "references" / "patterns.md").read_text()
        # top-level examples are fences without a language tag; their content is indented code
        blocks = re.findall(r"^```\n(.*?)^```$", text, re.M | re.S)
        self.assertGreaterEqual(len(blocks), 8)
        # components the snippets refer to but do not define
        project = {"hr.v1": apx.Component("restModule", "hr.v1")}
        for block in blocks:
            code = textwrap.dedent(block)
            if code.startswith("handler"):
                code = "restModule m (\n\n    template t (\n\n" + textwrap.indent(code, "        ") + "\n    )\n\n)\n"
            with self.subTest(code.splitlines()[0]):
                self.assertEqual(problems(code, project), [], code)


if __name__ == "__main__":
    unittest.main()
