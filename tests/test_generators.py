"""REST Data Source and OpenAPI generation, checked with the tools that consume them."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ords2apx import apx
from ords2apx.openapi import generate as openapi
from ords2apx.restsource import generate as rest_sources

ROOT = Path(__file__).parent
EXAMPLE = ROOT.parent / "examples" / "hr_export.ords.apx"
APP = ROOT / "fixtures" / "apex-app"
URL = "https://db.example.com/ords/hr/"
SQLCL = os.environ.get("SQLCL_BIN") or shutil.which("sql")

try:
    from openapi_spec_validator import validate as validate_openapi
except ImportError:
    validate_openapi = None


def example():
    return [apx.loads(EXAMPLE.read_text())]


class RestSources(unittest.TestCase):
    def test_shape(self):
        files, skipped = rest_sources(example(), URL)
        self.assertEqual(sorted(files), [
            "shared-components/rest-data-sources/hr-v1-employees.apx",
            "workspace-components/rest-data-source-servers/db-example-com-ords-hr.apx",
        ])
        src = apx.loads(files["shared-components/rest-data-sources/hr-v1-employees.apx"]).components[0]
        self.assertEqual(src.get("urlPathPrefix", "source"), apx.Text("/hr/v1/employees/"))
        ops = {c.id: (c.get("httpMethod", "operation").value, c.get("urlPattern", "operation").value)
               for c in src.children if c.kind == "operation"}
        self.assertEqual(ops, {"get": ("get", "."), "post": ("post", "."), "get-id": ("get", ":id"),
                               "delete-id": ("delete", ":id")})
        self.assertEqual(skipped, ["hr.v1 departments/report.csv GET: csvQuery is not JSON"])

    @unittest.skipUnless(SQLCL, "SQLcl not found (set SQLCL_BIN)")
    def test_apex_validate_accepts_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "app"
            shutil.copytree(APP, app)
            files, _ = rest_sources(example(), URL)
            for rel, text in files.items():
                (app / rel).parent.mkdir(parents=True, exist_ok=True)
                (app / rel).write_text(text)
            out = subprocess.run([SQLCL, "/nolog"], input=f'apex validate -input "{app}"\nexit\n',
                                 capture_output=True, text=True, timeout=300).stdout
            self.assertIn("Validation successful", out, out)
            self.assertNotIn("FILE_IGNORED", out, out)


class OpenApi(unittest.TestCase):
    def test_paths_and_security(self):
        spec = openapi(example(), URL)
        self.assertEqual(list(spec["paths"]), ["/hr/v1/employees/", "/hr/v1/employees/{id}", "/hr/v1/departments/report.csv"])
        get = spec["paths"]["/hr/v1/employees/"]["get"]
        self.assertEqual(get["parameters"], [{"name": "dept", "in": "query", "schema": {"type": "integer"}}])
        self.assertEqual(get["security"], [{"ords_oauth2": ["hr.v1.priv"]}])
        # the privilege names the whole module, so every path in it is protected
        self.assertIn("security", spec["paths"]["/hr/v1/departments/report.csv"]["get"])
        flow = spec["components"]["securitySchemes"]["ords_oauth2"]["flows"]["clientCredentials"]
        self.assertEqual(flow["tokenUrl"], "https://db.example.com/ords/hr/oauth/token")

    @unittest.skipIf(validate_openapi is None, "openapi-spec-validator not installed")
    def test_valid_openapi(self):
        validate_openapi(openapi(example(), URL))


if __name__ == "__main__":
    unittest.main()
