"""Against a real database with ORDS: deploy, plan, pull, prune, and call the API.

Runs when ORDS2APX_TEST_DSN is set (scripts/test-db.sh starts a throwaway
Oracle Free + ORDS and prints it). The schema in the DSN is wiped of ORDS
definitions first, so never point this at a database you care about.
"""

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from ords2apx import apx
from ords2apx.cli import main

DSN = os.environ.get("ORDS2APX_TEST_DSN")
ORDS_URL = os.environ.get("ORDS2APX_TEST_ORDS_URL")
EXAMPLE = Path(__file__).parent.parent / "examples" / "hr_export.ords.apx"

EXTRA = """
restModule legacy (
    basePath: /legacy/
    status: published

    template ping (

        handler GET (
            source {
                type: query
                sqlQuery:
                    ```sql
                    select 1 as ok from dual
                    ```
            }
        )

    )

)

restJwtProfile (
    issuer: https://idp.example.com/
    audience: hr-api
    jwkUrl: https://idp.example.com/.well-known/jwks.json
)
"""


def run(*args: str) -> int:
    return main([*args, "--dsn", DSN])


@unittest.skipUnless(DSN, "set ORDS2APX_TEST_DSN (see scripts/test-db.sh)")
class Database(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ords2apx.db import connect
        cls.conn = connect(DSN)
        cur = cls.conn.cursor()
        for ddl in ("create table departments (department_id number primary key, department_name varchar2(30))",
                    "create table employees (employee_id number primary key, first_name varchar2(30), "
                    "last_name varchar2(30), email varchar2(60), department_id number, hire_date date, job_id varchar2(10))"):
            try:
                cur.execute(ddl)
            except Exception:
                pass  # already there
        # start from a schema with nothing of ours in it
        empty = Path(tempfile.mkdtemp())
        (empty / "schema.ords.apx").write_text(
            "restSchema (\n    enabled: true\n    autoRestAuth: false\n    urlMapping {\n"
            "        type: basePath\n        pattern: hr\n    }\n)\n")
        assert run("deploy", str(empty), "--prune", "--yes") == 0

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def project(self) -> Path:
        d = Path(tempfile.mkdtemp())
        # the test user is not HR
        text = EXAMPLE.read_text().replace("    schema: HR\n", "").replace("    owner: HR\n", "")
        doc = apx.loads(text + EXTRA)
        from ords2apx.project import write_split
        write_split(doc, d)
        return d

    def plan(self, path: Path, *extra: str) -> int:
        return run("plan", str(path), "--detailed-exitcode", *extra)

    def test_deploy_plan_pull_prune(self):
        project = self.project()

        self.assertEqual(self.plan(project), 2, "a fresh schema needs changes")
        self.assertEqual(run("deploy", str(project), "--yes"), 0)
        # export -> convert -> compare reproduces exactly what was deployed
        self.assertEqual(self.plan(project), 0, "deploying then planning shows no changes")

        pulled = Path(tempfile.mkdtemp()) / "rest"
        self.assertEqual(run("pull", "-d", str(pulled)), 0)
        self.assertEqual(self.plan(pulled), 0, "a pulled project matches the database")
        self.assertEqual(sorted(p.name for p in pulled.rglob("*.ords.apx")),
                         ["hr.v1.ords.apx", "legacy.ords.apx", "schema.ords.apx", "security.ords.apx"])

        # change a module and drop a role: a change, and a removal only with --prune
        module = project / "modules" / "legacy.ords.apx"
        module.write_text(module.read_text().replace("select 1 as ok", "select 2 as ok"))
        security = project / "security.ords.apx"
        sec = apx.loads(security.read_text())
        sec.components = [c for c in sec.components if not (c.kind == "restJwtProfile")]
        security.write_text(apx.dumps(sec))
        self.assertEqual(run("deploy", str(project), "--yes"), 0)
        self.assertEqual(self.plan(project), 0, "without --prune the JWT profile stays, and is not a change")
        self.assertEqual(self.plan(project, "--prune"), 2)
        self.assertEqual(run("deploy", str(project), "--prune", "--yes"), 0)
        self.assertEqual(self.plan(project, "--prune"), 0)

        # ORDS's own AutoREST privileges survive --prune
        cur = self.conn.cursor()
        count = cur.execute("select count(*) from user_ords_privileges where name like 'oracle.dbtools.autorest%'").fetchone()[0]
        self.assertGreater(count, 0)

    @unittest.skipUnless(ORDS_URL, "set ORDS2APX_TEST_ORDS_URL to call the deployed API")
    def test_deployed_api_answers(self):
        project = self.project()
        self.assertEqual(run("deploy", str(project), "--yes"), 0)
        with urllib.request.urlopen(ORDS_URL.rstrip("/") + "/hr/legacy/ping", timeout=30) as r:
            body = json.load(r)
        self.assertEqual(body["items"], [{"ok": 1}])
        # hr.v1 is protected by a privilege
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(ORDS_URL.rstrip("/") + "/hr/hr/v1/employees/", timeout=30)
        self.assertEqual(e.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
