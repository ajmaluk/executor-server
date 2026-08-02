import os
import sys
import time
from flask import Blueprint, jsonify

try:
    from config import ServerConfig
except ImportError:
    from server.config import ServerConfig

health_bp = Blueprint("health", __name__)
START_TIME = time.time()


def _get_memory_usage_mb():
    """Gets process resident set size (RSS) memory usage in MB."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(usage / (1024 * 1024), 2)
        return round(usage / 1024, 2)
    except Exception:
        return None


@health_bp.route("/", methods=["GET"])
def index():
    """Root service information endpoint."""
    return jsonify({
        "service": ServerConfig.SERVER_NAME,
        "status": "online",
        "engine": "native_standalone",
        "version": ServerConfig.SERVER_VERSION,
        "endpoints": {
            "health": "/health",
            "healthz": "/healthz",
            "readiness": "/readyz",
            "status": "/api/v1/status",
            "languages": "/api/v1/languages",
            "execute": "/api/v1/execute"
        },
        "docs": "Send execution requests with X-API-Key or Authorization header to /api/v1/execute"
    }), 200


@health_bp.route("/health", methods=["GET"])
@health_bp.route("/healthz", methods=["GET"])
@health_bp.route("/livez", methods=["GET"])
def health_check():
    """Liveness probe endpoint returning deep system metrics."""
    uptime_seconds = round(time.time() - START_TIME, 2)
    mem_mb = _get_memory_usage_mb()

    return jsonify({
        "status": "healthy",
        "service": ServerConfig.SERVER_NAME,
        "engine": "native_standalone",
        "version": ServerConfig.SERVER_VERSION,
        "environment": ServerConfig.ENVIRONMENT,
        "uptime_seconds": uptime_seconds,
        "python_version": sys.version.split()[0],
        "memory_rss_mb": mem_mb,
        "pid": os.getpid()
    }), 200


@health_bp.route("/readyz", methods=["GET"])
def readiness_check():
    """Readiness probe testing server availability."""
    return jsonify({
        "status": "ready",
        "service": ServerConfig.SERVER_NAME,
        "engine": "native_standalone"
    }), 200


@health_bp.route("/api/v1/status", methods=["GET"])
def api_status():
    """Detailed Code Executor configuration status."""
    return jsonify({
        "status": "ok",
        "service": ServerConfig.SERVER_NAME,
        "engine": "native_standalone",
        "version": ServerConfig.SERVER_VERSION,
        "environment": ServerConfig.ENVIRONMENT,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "limits": {
            "max_code_bytes": ServerConfig.EXECUTOR_MAX_CODE_BYTES,
            "max_stdin_bytes": ServerConfig.EXECUTOR_MAX_STDIN_BYTES,
            "max_output_bytes": ServerConfig.EXECUTOR_MAX_OUTPUT_BYTES,
            "timeout_s": ServerConfig.EXECUTOR_TIMEOUT_S
        },
        "allowed_origin_rules": [
            "Configured ALLOWED_ORIGINS",
            "toolpix.pythonanywhere.com",
            "*.uthakkan.in (uthakkan.in and all subdomains)"
        ],
        "auth_configured": bool(ServerConfig.GLOBAL_API_KEY)
    }), 200
