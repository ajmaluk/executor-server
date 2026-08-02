from flask import Blueprint, jsonify

from server.auth import require_api_key

auth_bp = Blueprint("auth_api", __name__, url_prefix="/api/v1/auth")


@auth_bp.route("/verify", methods=["GET", "POST", "OPTIONS"])
@require_api_key
def verify_key():
    """Validates if the provided API Key is recognized and active."""
    return jsonify({
        "valid": True,
        "status": "authenticated",
        "message": "API key is valid and authorized for multi-website requests."
    }), 200
