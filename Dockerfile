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

# Copy dependency files. README.md is included because pyproject.toml declares
# `readme = "README.md"`, which hatchling validates while building the package.
COPY pyproject.toml uv.lock README.md ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy the application code. The data/ directory is NOT baked into the image —
# it is provided at runtime by the `supps-data` named volume (see docker-compose.yml).
COPY skg/ skg/

# Expose the FastAPI port
EXPOSE 8000

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Command to run the application
CMD ["uvicorn", "skg.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
