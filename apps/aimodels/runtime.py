"""Resolve which endpoint to call for a given job.

Order: the active default row in the database, then the environment variables that were
there before this page existed. Everything else in the app asks through here, so changing
a provider in the UI takes effect on the next call with no redeploy.
"""

import os

from django.core.cache import cache

CACHE_KEY = "aimodels:config:v1"
CACHE_TTL = 60


def env_fallback(kind):
    if kind == "llm":
        return {
            "name": "Environment (LLM_*)",
            "base_url": os.environ.get("LLM_BASE_URL", "http://172.16.15.111:7879/v1").rstrip("/"),
            "api_key": os.environ.get("LLM_API_KEY", "ollama"),
            "model": os.environ.get("LLM_MODEL", "RedHatAI/Qwen3.6-35B-A3B-NVFP4"),
            "timeout": float(os.environ.get("LLM_TIMEOUT", "300")),
            "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "8000")),
            "context_tokens": int(os.environ.get("LLM_CONTEXT_TOKENS", "16000")),
            "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.2")),
            "language": "",
            "extra": {},
            "httpx": {},
            "source": "environment",
        }
    if kind == "asr":
        return {
            "name": "Environment (ASR_*)",
            "base_url": os.environ.get("ASR_BASE_URL", "http://172.16.15.111:7600/v1").rstrip("/"),
            "api_key": os.environ.get("ASR_API_KEY", "cant-be-empty"),
            "model": os.environ.get("ASR_MODEL", "Qwen/Qwen3-ASR-0.6B"),
            "timeout": float(os.environ.get("AUDIO_TIMEOUT", "900")),
            "max_tokens": 0,
            "temperature": 0.0,
            "language": os.environ.get("ASR_LANGUAGE", ""),
            "extra": {},
            "httpx": {},
            "source": "environment",
        }
    if kind == "diarization":
        return {
            "name": "Environment (DIAR_*)",
            "base_url": os.environ.get("ASR_URL", "http://asr:8200/v1").rstrip("/"),
            "api_key": "",
            "model": os.environ.get("DIAR_MODEL", "nvidia/multitalker-parakeet-streaming-0.6b-v1"),
            "timeout": float(os.environ.get("AUDIO_TIMEOUT", "900")),
            "max_tokens": 0, "temperature": 0.0, "language": "", "extra": {}, "httpx": {},
            "source": "environment",
        }
    if kind == "embedding":
        return {
            "name": "Environment (EMBEDDING_*)",
            "base_url": os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/"),
            "api_key": os.environ.get("EMBEDDING_API_KEY", ""),
            "model": os.environ.get("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"),
            "timeout": float(os.environ.get("EMBEDDING_TIMEOUT", "60")),
            "max_tokens": 0, "temperature": 0.0, "language": "", "extra": {}, "httpx": {},
            "source": "environment",
        }
    if kind == "reranker":
        return {
            "name": "Environment (RERANKER_*)",
            "base_url": os.environ.get("RERANKER_BASE_URL", "").rstrip("/"),
            "api_key": os.environ.get("RERANKER_API_KEY", ""),
            "model": os.environ.get("RERANKER_MODEL", "Qwen/Qwen3-Reranker-8B"),
            "timeout": float(os.environ.get("RERANKER_TIMEOUT", "60")),
            "max_tokens": 0, "temperature": 0.0, "language": "", "extra": {}, "httpx": {},
            "source": "environment",
        }
    if kind == "denoise":
        return {
            "name": "Environment (DENOISE_*)",
            "base_url": os.environ.get("DENOISE_URL", "http://denoise:8100/v1").rstrip("/"),
            "api_key": "",
            "model": os.environ.get("DENOISE_MODEL", "weya-ai/hush"),
            "timeout": float(os.environ.get("AUDIO_TIMEOUT", "900")),
            "max_tokens": 0, "temperature": 0.0, "language": "", "extra": {}, "httpx": {},
            "source": "environment",
        }
    return {}


def config_for(kind, use_cache=True):
    """-> dict for the active provider of `kind`, falling back to the environment."""
    if use_cache:
        cached = cache.get(CACHE_KEY) or {}
        if kind in cached:
            return cached[kind]

    from .models import ModelProvider

    try:
        row = (
            ModelProvider.objects.filter(kind=kind, is_active=True)
            .order_by("-is_default", "name")
            .first()
        )
    except Exception:  # noqa: BLE001 - table may not exist yet during the first migrate
        row = None

    config = row.as_config() if row else env_fallback(kind)
    if use_cache:
        cached = cache.get(CACHE_KEY) or {}
        cached[kind] = config
        cache.set(CACHE_KEY, cached, CACHE_TTL)
    return config


def invalidate():
    cache.delete(CACHE_KEY)
