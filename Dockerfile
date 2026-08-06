FROM python:3.11-slim

# Install system packages & compilers (Java OpenJDK 17, GCC, G++, Go, Rust, PHP, Ruby, Perl, SQLite)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk-headless \
    gcc \
    g++ \
    golang \
    rustc \
    php-cli \
    ruby \
    perl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
