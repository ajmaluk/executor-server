import threading
import time
from collections import defaultdict
from flask import jsonify, request

try:
    from config import ServerConfig
except ImportError:
    from server.config import ServerConfig


class RateLimiter:
    """Thread-safe fixed-window rate limiter per API key or client IP."""

    def __init__(self, window_seconds: int = 60, max_requests: int = 10000):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._requests = defaultdict(lambda: [0.0, 0])

    def is_allowed(self, identifier: str) -> tuple[bool, int, int]:
        now = time.time()
        with self._lock:
            start_time, count = self._requests[identifier]

            if now - start_time >= self.window_seconds:
                self._requests[identifier] = [now, 1]
                return True, self.max_requests - 1, self.window_seconds

            if count < self.max_requests:
                self._requests[identifier][1] += 1
                return True, self.max_requests - (count + 1), int(self.window_seconds - (now - start_time))

            return False, 0, max(1, int(self.window_seconds - (now - start_time)))


limiter = RateLimiter(
    window_seconds=ServerConfig.RATE_LIMIT_WINDOW_S,
    max_requests=ServerConfig.RATE_LIMIT_MAX_REQUESTS
)


def apply_rate_limit(f):
    """Decorator to enforce rate limiting on endpoints if RATE_LIMIT_ENABLED is True."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == "OPTIONS" or not ServerConfig.RATE_LIMIT_ENABLED:
            return f(*args, **kwargs)

        client_id = request.headers.get("X-API-Key") or request.remote_addr or "anonymous"
        allowed, remaining, reset_s = limiter.is_allowed(client_id)

        if not allowed:
            response = jsonify({
                "error": "Too Many Requests",
                "message": f"Rate limit exceeded. Try again in {reset_s} seconds."
            })
            response.headers["Retry-After"] = str(reset_s)
            response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(reset_s)
            return response, 429

        response_or_tuple = f(*args, **kwargs)

        if isinstance(response_or_tuple, tuple):
            resp, status = response_or_tuple[0], response_or_tuple[1]
            if hasattr(resp, "headers"):
                resp.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
                resp.headers["X-RateLimit-Remaining"] = str(remaining)
                resp.headers["X-RateLimit-Reset"] = str(reset_s)
            return resp, status

        if hasattr(response_or_tuple, "headers"):
            response_or_tuple.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
            response_or_tuple.headers["X-RateLimit-Remaining"] = str(remaining)
            response_or_tuple.headers["X-RateLimit-Reset"] = str(reset_s)

        return response_or_tuple

    return decorated
