FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application
COPY openproxy/ openproxy/

# Create data directory (mounted as a volume at runtime)
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check — Docker will restart the container if this fails 3 times
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run with uvicorn
CMD ["uvicorn", "openproxy.main:app", "--host", "0.0.0.0", "--port", "8000"]
