FROM python:3.11-slim

# The only model that runs locally is the diarizer (pyannote/speaker-diarization-3.1).
# Speech-to-text is a remote OpenAI-compatible endpoint, so no ASR weights live here.
# INSTALL_DIARIZER=0 keeps the image small and the service falls back to mock turns.
ARG INSTALL_DIARIZER=1

ENV PYTHONUNBUFFERED=1 HF_HOME=/models

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# One layer per install: the CUDA wheels are ~1 GB, so a failure further down must not
# re-download them. A short default pip timeout aborts the build mid-download.
ENV PIP_RETRIES=20 PIP_TIMEOUT=180 PIP_NO_CACHE_DIR=1

# pyannote pulls the torch build it wants; installing torch first only risks an ABI
# mismatch with torchaudio, which is what broke the NeMo attempt.
#
# 4.0.7, not 3.3.2: 3.3.2 imports torchaudio.AudioMetaData, removed in torchaudio 2.9,
# so it fails to import against a current torch. 4.x drops the torchaudio soundfile
# backend for torchcodec and takes `token=` instead of the removed `use_auth_token=`
# (see load_diarizer in app.py).
RUN if [ "$INSTALL_DIARIZER" = "1" ]; then pip install "pyannote.audio==4.0.7" ; fi

COPY app.py .

EXPOSE 8200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
