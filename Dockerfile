FROM python:3.11-slim

# Set environment variables for Python & Render execution
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000 \
    JAVA_HOME=/usr/lib/jvm/default-java \
    GOCACHE=/opt/gocache \
    GOTMPDIR=/tmp \
    GOMAXPROCS=2 \
    CGO_ENABLED=0 \
    GO111MODULE=off

# Install essential runtime tools & compilers in a single layer with apt cache cleanup
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    default-jdk-headless \
    gcc \
    g++ \
    golang \
    rustc \
    php-cli \
    ruby \
    perl \
    sqlite3 \
    nodejs \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install standalone Kotlin compiler & runner
RUN curl -sSL "https://github.com/JetBrains/kotlin/releases/download/v1.9.24/kotlin-compiler-1.9.24.zip" -o /tmp/kotlinc.zip \
    && unzip -q /tmp/kotlinc.zip -d /opt \
    && rm /tmp/kotlinc.zip \
    && ln -s /opt/kotlinc/bin/kotlinc /usr/local/bin/kotlinc \
    && ln -s /opt/kotlinc/bin/kotlin /usr/local/bin/kotlin

# Pre-compile Go standard library cache into /opt/gocache for instant compilation
RUN mkdir -p /opt/gocache && GOCACHE=/opt/gocache CGO_ENABLED=0 go build std || true && chmod -R 777 /opt/gocache

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
