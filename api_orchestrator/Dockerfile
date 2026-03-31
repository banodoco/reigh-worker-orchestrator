# Railway Dockerfile for API Orchestrator
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Install dependencies (path relative to project root build context)
COPY api_orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application packages
COPY api_orchestrator/ /app/api_orchestrator/
COPY orchestrator_common/ /app/orchestrator_common/

# Create non-root user for security
RUN useradd -m -u 1000 apiworker && chown -R apiworker:apiworker /app
USER apiworker

# Default command - runs the API orchestrator as a module
CMD ["python", "-m", "api_orchestrator.main"]
