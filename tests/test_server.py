import json
import unittest
from unittest.mock import MagicMock, patch

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
        """Readiness endpoint should check Piston connectivity."""
        with patch.object(health_mod._health_session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [{"language": "python", "version": "3.10.0"}]
            mock_get.return_value = mock_resp

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

    def test_execute_with_stdin_and_args(self):
        """Execution request with stdin and args should pass sanitized payload to Piston."""
        with patch.object(executor_api_mod._session, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "run": {"stdout": "Input: Hello\nArgs: arg1\n", "stderr": "", "code": 0},
                "compile": {"output": ""}
            }
            mock_post.return_value = mock_response

            res = self.client.post(
                "/api/v1/execute",
                headers={"X-API-Key": "test-secret-key"},
                json={
                    "language": "py",
                    "code": "import sys; print(f'Input: {sys.stdin.read().strip()}'); print(f'Args: {sys.argv[1]}')",
                    "stdin": "Hello",
                    "args": ["arg1"]
                }
            )

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("language"), "python")
            self.assertIn("Input: Hello", data.get("stdout"))

    def test_rate_limit_optional(self):
        """Rate limiting headers present when RATE_LIMIT_ENABLED=True."""
        ServerConfig.RATE_LIMIT_ENABLED = True
        with patch.object(executor_api_mod._session, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "run": {"stdout": "OK\n", "stderr": "", "code": 0},
                "compile": {"output": ""}
            }
            mock_post.return_value = mock_response

            res = self.client.post(
                "/api/v1/execute",
                headers={"X-API-Key": "test-secret-key"},
                json={"language": "python", "code": "print('OK')"}
            )

            self.assertEqual(res.status_code, 200)
            self.assertIn("X-RateLimit-Remaining", res.headers)
            ServerConfig.RATE_LIMIT_ENABLED = False

    def test_execute_multi_file_support(self):
        """Execution request with 'files' array should process multi-file input."""
        with patch.object(executor_api_mod._session, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "run": {"stdout": "Multi-file Success\n", "stderr": "", "code": 0},
                "compile": {"output": ""}
            }
            mock_post.return_value = mock_response

            res = self.client.post(
                "/api/v1/execute",
                headers={"X-API-Key": "test-secret-key"},
                json={
                    "language": "python",
                    "files": [
                        {"name": "main.py", "content": "import helper; helper.greet()"},
                        {"name": "helper.py", "content": "def greet(): print('Multi-file Success')"}
                    ]
                }
            )

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("files_executed"), ["main.py", "helper.py"])

    def test_java_public_class_detection(self):
        """Java code with 'public final class Solution' should produce 'Solution.java' filename."""
        with patch.object(executor_api_mod._session, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "run": {"stdout": "Hello Java Solution\n", "stderr": "", "code": 0},
                "compile": {"output": ""}
            }
            mock_post.return_value = mock_response

            java_code = "public final class Solution { public static void main(String[] args) {} }"
            res = self.client.post(
                "/api/v1/execute",
                headers={"X-API-Key": "test-secret-key"},
                json={"language": "java", "code": java_code}
            )

            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("filename"), "Solution.java")


if __name__ == "__main__":
    unittest.main()
