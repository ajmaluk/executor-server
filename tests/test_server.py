import json
import unittest

try:
    import app as server_app
    import config as server_config
    import cors as server_cors
    import blueprints.executor_api as executor_api_mod
    import blueprints.health as health_mod
except ImportError:
    import server.app as server_app
    import server.config as server_config
    import server.cors as server_cors
    import server.blueprints.executor_api as executor_api_mod
    import server.blueprints.health as health_mod

create_app = server_app.create_app
ServerConfig = server_config.ServerConfig
is_allowed_origin = server_cors.is_allowed_origin


class TestCodeExecutorServer(unittest.TestCase):
    def setUp(self):
        ServerConfig.API_SECRETS = ("test-secret-key", "site2-secret-key")
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
        """Native Python execution should execute python code cleanly without external API."""
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-secret-key"},
            json={
                "language": "py",
                "code": "import sys; print('Native Execution Test'); print(f'Input: {sys.stdin.read().strip()}')",
                "stdin": "Hello World"
            }
        )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("engine"), "native_standalone")
        self.assertEqual(data.get("language"), "python")
        self.assertIn("Native Execution Test", data.get("stdout"))
        self.assertIn("Input: Hello World", data.get("stdout"))

    def test_execute_native_sqlite_code(self):
        """Native SQLite execution should execute queries in memory."""
        sql = "CREATE TABLE users (id INT, name TEXT); INSERT INTO users VALUES (1, 'Alice'); SELECT * FROM users;"
        res = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": "test-secret-key"},
            json={
                "language": "sql",
                "code": sql
            }
        )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("Alice", data.get("stdout"))


if __name__ == "__main__":
    unittest.main()
