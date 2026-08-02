import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _load_env():
    base_dir = Path(__file__).resolve().parent
    for candidate in (base_dir / ".env.local", base_dir / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
            break


_load_env()


class ServerConfig:
    """Configuration settings for the Dedicated Standalone Code Executor Backend Server on Render."""
    
    # Global Shared API Key for all client websites
    # Priority: GLOBAL_API_KEY > SERVER_API_KEY > API_SECRET
    GLOBAL_API_KEY = (
        os.environ.get("GLOBAL_API_KEY") or
        os.environ.get("SERVER_API_KEY") or
        os.environ.get("API_SECRET") or
        ""
    ).strip()
    
    # Default allowed origins (comma-separated list of domain URLs)
    _raw_origins = os.environ.get("ALLOWED_ORIGINS", "https://toolpix.pythonanywhere.com,https://uthakkan.in,*")
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

    # Native Code Executor Limits & Settings
    EXECUTOR_MAX_CODE_BYTES = int(os.environ.get("EXECUTOR_MAX_CODE_BYTES", "65536"))  # 64 KB limit
    EXECUTOR_MAX_OUTPUT_BYTES = int(os.environ.get("EXECUTOR_MAX_OUTPUT_BYTES", "200000"))  # 200 KB limit
    EXECUTOR_MAX_STDIN_BYTES = int(os.environ.get("EXECUTOR_MAX_STDIN_BYTES", "10000"))
    EXECUTOR_TIMEOUT_S = int(os.environ.get("EXECUTOR_TIMEOUT_S", "30"))

    # Rate Limiting Configuration (Disabled by default: RATE_LIMIT_ENABLED=false)
    RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    RATE_LIMIT_WINDOW_S = int(os.environ.get("RATE_LIMIT_WINDOW_S", "60"))
    RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "10000"))  # 10,000 req/min
    
    # Server Metadata
    SERVER_NAME = "ToolPix Native Standalone Code Executor"
    SERVER_VERSION = "2.1.0"
    ENVIRONMENT = os.environ.get("RENDER_SERVICE_TYPE", os.environ.get("FLASK_ENV", "production"))
