FROM python:3.11-slim

# INSTALL_HUSH=1 pulls torch + DeepFilterLib so the real weya-ai/hush weights can run.
# 0 keeps the image small; the service then uses the spectral-gating fallback.
ARG INSTALL_HUSH=0
# CPU wheels by default. For a GPU host, build with a CUDA index so hush can use the
# card (the service still falls back to CPU at runtime if the GPU is unavailable):
#   --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 HF_HOME=/models

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN if [ "$INSTALL_HUSH" = "1" ]; then \
      pip install --no-cache-dir --index-url "$TORCH_INDEX_URL" torch==2.5.1 && \
      pip install --no-cache-dir DeepFilterLib soundfile huggingface_hub ; \
    fi

COPY app.py .

EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
