import hmac
import logging
from functools import wraps

from flask import jsonify, request

try:
    from config import ServerConfig
except ImportError:
    from server.config import ServerConfig

logger = logging.getLogger(__name__)


def extract_api_key_from_request():
    """Extracts Global API Key from HTTP Headers.
    Supports:
      - X-API-Key: <key>
      - Authorization: Bearer <key>
      - Query Parameter: ?api_key=<key> (Optional fallback)
    """
    key = request.headers.get("X-API-Key")
    if key:
        return key.strip()

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    query_key = request.args.get("api_key")
    if query_key:
        return query_key.strip()

    return None


def verify_api_key(provided_key):
    """Verifies provided key against configured GLOBAL_API_KEY using timing-safe comparison."""
    if not provided_key:
        return False

    global_key = ServerConfig.GLOBAL_API_KEY
    if not global_key:
        logger.warning("No GLOBAL_API_KEY / API_SECRET configured in environment.")
        return False

    return hmac.compare_digest(provided_key, global_key)


def require_api_key(f):
    """Decorator to protect API endpoints with Global API Key authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200

        provided_key = extract_api_key_from_request()
        
        if not provided_key:
            return jsonify({
                "error": "Unauthorized",
                "message": "Missing API Key. Provide 'X-API-Key' header or 'Authorization: Bearer <key>'."
            }), 401

        if not verify_api_key(provided_key):
            return jsonify({
                "error": "Forbidden",
                "message": "Invalid Global API Key provided."
            }), 403

        return f(*args, **kwargs)

    return decorated_function
