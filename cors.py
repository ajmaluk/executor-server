import re
from urllib.parse import urlparse

from flask import request

try:
    from config import ServerConfig
except ImportError:
    from server.config import ServerConfig

_AUTHAKKAN_REGEX = re.compile(r"^(?:[a-zA-Z0-9-]+\.)*uthakkan\.in$", re.IGNORECASE)


def is_allowed_origin(origin: str) -> bool:
    """Checks if request origin is authorized.
    
    Allowed origins include:
    1. Configured origins in ALLOWED_ORIGINS (or '*')
    2. 'toolpix.pythonanywhere.com'
    3. Any subdomain of '.uthakkan.in' (e.g. 'uthakkan.in', 'api.uthakkan.in', 'code.uthakkan.in')
    """
    if not origin:
        return True

    allowed_list = ServerConfig.ALLOWED_ORIGINS
    if "*" in allowed_list or origin in allowed_list:
        return True

    try:
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()

        if not hostname:
            return False

        if hostname == "toolpix.pythonanywhere.com":
            return True

        if _AUTHAKKAN_REGEX.match(hostname):
            return True

    except Exception:
        pass

    return False


def setup_cors_headers(response):
    """Applies CORS headers to HTTP responses for allowed origin domains."""
    origin = request.headers.get("Origin")

    if is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key, Accept, Origin"
        response.headers["Access-Control-Max-Age"] = "86400"
    
    return response
