FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application
COPY openproxy/ openproxy/
COPY data/ data/

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "openproxy.main:app", "--host", "0.0.0.0", "--port", "8000"]
