# ============================================================
# Yurika Backend — Hugging Face Space Dockerfile
# ============================================================

FROM python:3.11-slim

# 1. Hugging Face Spaces require running as a non-root user (id 1000)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# 2. Switch back to root temporarily to install system dependencies (PostgreSQL libs, etc.)
USER root
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*
USER user

# 3. Copy requirements first to leverage Docker cache
COPY --chown=user:user requirements.txt .

# 4. Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application
COPY --chown=user:user . .

# 6. Expose the port required by Hugging Face Spaces
EXPOSE 7860

# 7. Start the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
