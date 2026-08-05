import os

# Gunicorn Configuration for Scalable Deployment on Render

bind = f"0.0.0.0:{os.environ.get('PORT', '5001')}"

# Worker processes & threading model
# gthread worker class enables non-blocking multithreaded request handling
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", os.environ.get("PYTHON_GET_THREADS", "10")))

# HTTP Keep-Alive settings for persistent connections
keepalive = 65
timeout = 60
graceful_timeout = 30

# Recycles worker processes periodically to prevent memory leaks over time
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Memory efficiency
preload_app = True
