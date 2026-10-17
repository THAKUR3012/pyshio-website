FROM python:3.11-slim

# Install system dependencies for PostgreSQL client, timezone data, and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    postgresql-client \
    tzdata \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create uploads directory and ensure entrypoint is executable
RUN mkdir -p /app/uploads && \
    chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYSHIO_UPLOAD_DIR=/app/uploads

EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]

CMD ["gunicorn", "-w", "3", "-k", "uvicorn.workers.UvicornWorker", "server:app", "--bind", "0.0.0.0:8080"]
