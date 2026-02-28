FROM python:3.12-slim

# Hugging Face Spaces require running as a non-root user.
# Create a user with UID 1000 and setup home and path.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface

WORKDIR $HOME/app

# Only copy requirements first to leverage Docker layer caching
COPY --chown=user requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-bake AI models into the Docker image.
# Hugging Face Spaces sleep when inactive. Pre-downloading ensures 
# the server boots instantly without downloading massive models on every wake.
RUN python -c "from FlagEmbedding import FlagModel; FlagModel('BAAI/bge-m3', device='cpu')"
RUN python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=False, device='cpu')"

# Copy the rest of the application code
COPY --chown=user . .

# Hugging Face Spaces explicitly route traffic to port 7860
EXPOSE 7860

# Start the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
