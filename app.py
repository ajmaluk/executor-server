import logging
from flask import Flask, jsonify

try:
    from blueprints.auth_api import auth_bp
    from blueprints.executor_api import executor_bp
    from blueprints.health import health_bp
    from config import ServerConfig
    from cors import setup_cors_headers
except ImportError:
    from server.blueprints.auth_api import auth_bp
    from server.blueprints.executor_api import executor_bp
    from server.blueprints.health import health_bp
    from server.config import ServerConfig
    from server.cors import setup_cors_headers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app():
    """Application factory for Dedicated Code Executor Backend Server on Render."""
    app = Flask(__name__)
    app.config.from_object(ServerConfig)
    
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    app.after_request(setup_cors_headers)

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(executor_bp)

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad Request", "message": str(e.description if hasattr(e, "description") else e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"error": "Unauthorized", "message": "Authentication required. Provide X-API-Key or Authorization header."}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden", "message": "Access denied. Invalid API Key provided."}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found", "message": "The requested API endpoint does not exist."}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method Not Allowed", "message": "HTTP method not allowed for this endpoint."}), 405

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return jsonify({"error": "Payload Too Large", "message": "HTTP request body exceeds maximum allowed 2MB limit."}), 413

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({"error": "Too Many Requests", "message": "Rate limit exceeded. Please retry shortly."}), 429

    @app.errorhandler(500)
    def internal_error(e):
        logger.exception("Internal Server Error")
        return jsonify({"error": "Internal Server Error", "message": "An unexpected server error occurred."}), 500

    return app


app = create_app()
application = app

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    logger.info("Starting Code Executor Backend Server locally on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=True)
