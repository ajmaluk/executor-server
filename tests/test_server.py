import unittest

try:
    import app as server_app
    import config as server_config
    import cors as server_cors
except ImportError:
    import server.app as server_app
    import server.config as server_config
    import server.cors as server_cors

create_app = server_app.create_app
ServerConfig = server_config.ServerConfig
is_allowed_origin = server_cors.is_allowed_origin


class TestCodeExecutorServer(unittest.TestCase):
    def setUp(self):
        ServerConfig.GLOBAL_API_KEY = "test-global-secret-key"
        ServerConfig.ALLOWED_ORIGINS = ["https://customsite.com"]
        ServerConfig.RATE_LIMIT_ENABLED = False
        
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_domain_allowlist(self):
        """Test CORS domain allowlist for toolpix.pythonanywhere.com and *.uthakkan.in."""
        self.assertTrue(is_allowed_origin("https://toolpix.pythonanywhere.com"))
        self.assertTrue(is_allowed_origin("http://toolpix.pythonanywhere.com"))
        self.assertTrue(is_allowed_origin("https://uthakkan.in"))
        self.assertTrue(is_allowed_origin("https://code.uthakkan.in"))
        self.assertTrue(is_allowed_origin("https://app.demo.uthakkan.in"))
        self.assertTrue(is_allowed_origin("https://customsite.com"))
        self.assertFalse(is_allowed_origin("https://unauthorized-domain.com"))

    def test_health_metrics_endpoint(self):
        """GET /health should return system metrics (memory_rss_mb, uptime_seconds, python_version)."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertIn("uptime_seconds", data)
        self.assertIn("python_version", data)

    def test_routes_match_with_realistic_host(self):
        """Routes must match with any Host header.

        Regression: ServerConfig.SERVER_NAME (a display label) was leaking into
        Flask's SERVER_NAME config via app.config.from_object(), which made
        Flask host-match against the label and 404 every real request.
        """
        for host in ("executor.uthakkan.in", "127.0.0.1:5001", "localhost"):
            res = self.client.get("/healthz", headers={"Host": host})
            self.assertEqual(res.status_code, 200, f"host {host} should reach /healthz")
            res2 = self.client.get("/api/v1/status", headers={"Host": host})
            self.assertEqual(res2.status_code, 200, f"host {host} should reach /api/v1/status")

    def test_readiness_endpoint(self):
        """Readiness endpoint should check server status."""
        res = self.client.get("/readyz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json().get("status"), "ready")

    def test_list_languages_and_aliases(self):
        """GET /api/v1/languages and GET /api/v1/languages/py should return registry info."""
        res = self.client.get("/api/v1/languages")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("python", data.get("languages", {}))

        res_alias = self.client.get("/api/v1/languages/py")
        self.assertEqual(res_alias.status_code, 200)
        alias_data = res_alias.get_json()
        self.assertEqual(alias_data.get("canonical_name"), "python")

    def test_execute_unauthorized_missing_key(self):
        """Execution request without API key should return 401."""
        res = self.client.post("/api/v1/execute", json={"language": "python", "code": "print(1)"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json().get("error"), "Unauthorized")

    def test_execute_forbidden_invalid_key(self):
        """Execution request with invalid API key should return 403."""
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "wrong-secret"},
            json={"language": "python", "code": "print(1)"}
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("error"), "Forbidden")

    def test_execute_native_python_code(self):
        """Native Python execution should execute python code cleanly using Global API Key."""
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={
                "language": "py",
                "code": "import sys; print('Global Key Execution Test'); print(f'Input: {sys.stdin.read().strip()}')",
                "stdin": "Hello World"
            }
        )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("engine"), "native_standalone")
        self.assertEqual(data.get("language"), "python")
        self.assertIn("Global Key Execution Test", data.get("stdout"))
        self.assertIn("Input: Hello World", data.get("stdout"))

    def test_execute_native_sqlite_code(self):
        """Native SQLite execution should execute queries using Global API Key."""
        sql = "CREATE TABLE users (id INT, name TEXT); INSERT INTO users VALUES (1, 'Alice'); SELECT * FROM users;"
        res = self.client.post(
            "/api/v1/execute",
            headers={"Authorization": "Bearer test-global-secret-key"},
            json={
                "language": "sql",
                "code": sql
            }
        )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("Alice", data.get("stdout"))

    def test_execute_sqlite_semicolon_inside_string(self):
        """Semicolons inside string literals must not split the statement."""
        sql = "CREATE TABLE t(x TEXT); INSERT INTO t VALUES ('foo;bar'); SELECT * FROM t;"
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={"language": "sql", "code": sql}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("foo;bar", data.get("stdout"))
        self.assertNotIn("unrecognized token", data.get("stderr", "").lower())

    def test_execute_sqlite_comments_and_multi_statement(self):
        """Comments containing semicolons and multi-line statements must execute cleanly."""
        sql = "CREATE TABLE u(y TEXT);\n-- comment; with semicolon\nINSERT INTO u VALUES ('a;''b;''c');\nSELECT y FROM u;"
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={"language": "sql", "code": sql}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("a;'b;'c", data.get("stdout"))

    def test_execute_sqlite_runaway_query_times_out(self):
        """A runaway recursive SQLite query must be killed by the timeout, not hang or crash the server."""
        import time
        ServerConfig.EXECUTOR_TIMEOUT_S = 2
        sql = "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM cnt) SELECT * FROM cnt;"
        start = time.time()
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={"language": "sql", "code": sql}
        )
        elapsed = time.time() - start
        self.assertLess(elapsed, 10, "SQL timeout did not bound execution")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("timed out", data.get("error", "").lower() or data.get("stderr", "").lower())
        ServerConfig.EXECUTOR_TIMEOUT_S = 30

    def test_execute_busy_returns_503(self):
        """When all execution slots are busy, a request must receive HTTP 503, not a 200 with an error."""
        try:
            from blueprints import executor_api
        except ImportError:
            from server.blueprints import executor_api

        original = executor_api._execution_semaphore.acquire
        executor_api._execution_semaphore.acquire = lambda *a, **kw: False
        try:
            res = self.client.post(
                "/api/v1/execute",
                headers={"X-API-Key": "test-global-secret-key"},
                json={"language": "python", "code": "print('x')"}
            )
        finally:
            executor_api._execution_semaphore.acquire = original

        self.assertEqual(res.status_code, 503)
        data = res.get_json()
        self.assertEqual(data.get("error"), "Service Unavailable")

    def test_execute_unsupported_language(self):
        """An unsupported language must return a 400 with a clear message."""
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={"language": "brainfuck", "code": "+++++."}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Unsupported language", res.get_json().get("message", ""))

    def test_execute_java_language_supported(self):
        """Java must be supported in the native executor registry."""
        res = self.client.get("/api/v1/languages/java")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("canonical_name"), "java")

    def test_piston_v2_endpoint_and_payload(self):
        """POST /v2/execute and POST /api/v2/execute with Piston files array payload should work."""
        for endpoint in ["/v2/execute", "/api/v2/execute"]:
            res = self.client.post(
                endpoint,
                headers={"X-API-Key": "test-global-secret-key"},
                json={
                    "language": "python",
                    "version": "3.10.0",
                    "files": [{"name": "main.py", "content": "print('Piston Python Test')"}]
                }
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn("run", data)
            self.assertEqual(data["run"]["code"], 0)
            self.assertEqual(data["run"]["stdout"].strip(), "Piston Python Test")

    def test_piston_v2_runtimes_endpoint(self):
        """GET /v2/runtimes and GET /api/v2/runtimes should return language runtimes list."""
        for endpoint in ["/v2/runtimes", "/api/v2/runtimes"]:
            res = self.client.get(endpoint)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertIn("languages", data)

    def test_execute_c_compile_and_run(self):
        """Compiled C must compile then run the resulting binary.

        Regression: the run command ('main.out', a relative path at index 0)
        was not resolved to the temp-dir path, so execvp failed to find it.
        """
        gcc = self._find_binary()
        if not gcc:
            self.skipTest("gcc/clang not installed")
        code = '#include <stdio.h>\nint main(){int s=0;for(int i=0;i<10;i++)s+=i;printf("sum=%d\\n",s);return 0;}'
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-global-secret-key"},
            json={"language": "c", "code": code}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success", data.get("stderr") or data.get("error"))
        self.assertEqual(data.get("stdout"), "sum=45\n")

    @staticmethod
    def _find_binary():
        import shutil
        return shutil.which("gcc") or shutil.which("clang")


if __name__ == "__main__":
    unittest.main()
