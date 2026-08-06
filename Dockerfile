FROM python:3.11-slim

# Set environment variables for Python & Render execution
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# Install essential runtime tools & compilers in a single layer with apt cache cleanup
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    openjdk-17-jdk-headless \
    gcc \
    g++ \
    golang-go \
    rustc \
    php-cli \
    ruby \
    perl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache Python dependencies layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose Render port
EXPOSE 10000

# Docker healthcheck targeting the executor /healthz endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:10000/healthz || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
