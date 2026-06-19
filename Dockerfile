# Use a slim Python 3.11 base image
FROM python:3.11-slim-bookworm

# Install curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy the application code and data
COPY skg/ skg/
COPY data/ data/
COPY README.md ./

# Expose the FastAPI port
EXPOSE 8000

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Command to run the application
CMD ["uvicorn", "skg.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
