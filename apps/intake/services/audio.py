"""Clients for the two OpenAI-compatible audio services (denoise + ASR/diarization)."""

import os

import httpx

DENOISE_URL = os.environ.get("DENOISE_URL", "http://denoise:8100/v1").rstrip("/")
ASR_URL = os.environ.get("ASR_URL", "http://asr:8200/v1").rstrip("/")
DENOISE_MODEL = os.environ.get("DENOISE_MODEL", "weya-ai/hush")
ASR_MODEL = os.environ.get("ASR_MODEL", "Qwen/Qwen3-ASR-0.6B")
DIAR_MODEL = os.environ.get("DIAR_MODEL", "nvidia/multitalker-parakeet-streaming-0.6b-v1")
# blank lets the model detect the language; set e.g. "de" to force one
ASR_LANGUAGE = os.environ.get("ASR_LANGUAGE", "")
TIMEOUT = float(os.environ.get("AUDIO_TIMEOUT", "900"))


class AudioServiceError(RuntimeError):
    pass


def diarization_settings():
    """Local diarizer settings: HF token and any speaker-count hints, from the AI models page."""
    from apps.aimodels.runtime import config_for

    config = config_for("diarization")
    extra = config.get("extra") or {}
    return {
        "hf_token": config.get("api_key", "") or os.environ.get("HF_TOKEN", ""),
        "num_speakers": str(extra.get("num_speakers", "") or ""),
        "min_speakers": str(extra.get("min_speakers", "") or ""),
        "max_speakers": str(extra.get("max_speakers", "") or ""),
    }


def asr_settings():
    """Remote speech-to-text endpoint chosen on the AI models page (env fallback)."""
    from apps.aimodels.runtime import config_for

    config = dict(config_for("asr"))
    provider_id = config.get("id")
    if provider_id:
        from apps.aimodels.models import ModelProvider

        provider = ModelProvider.objects.filter(pk=provider_id).first()
        if provider:
            # the asr container reads the same ./media mount, so a path is enough
            config["ca_cert_path"] = provider.cert_path("ca_cert") or ""
            config["verify_ssl"] = provider.verify_ssl
    return config


def denoise(content: bytes, filename: str):
    """-> (wav_bytes, meta). Noise reduction via the hush service."""
    try:
        response = httpx.post(
            f"{DENOISE_URL}/audio/enhancements",
            files={"file": (filename, content, "application/octet-stream")},
            data={"model": DENOISE_MODEL, "response_format": "wav"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise AudioServiceError(f"denoise service unreachable at {DENOISE_URL}: {exc}") from exc
    if response.status_code >= 400:
        raise AudioServiceError(f"denoise failed ({response.status_code}): {response.text[:300]}")
    return response.content, {
        "engine": response.headers.get("x-engine", "unknown"),
        "duration": float(response.headers.get("x-duration", 0) or 0),
    }


def transcribe(content: bytes, filename: str, diarize=True, language=None):
    """-> dict with text, segments[{start,end,text,speaker}], speakers[], engine.

    The service diarizes locally, slices the audio per speaker turn and sends each slice
    to the remote multi-language ASR model.
    """
    asr = asr_settings()
    diar = diarization_settings()
    try:
        response = httpx.post(
            f"{ASR_URL}/audio/transcriptions",
            files={"file": (filename, content, "application/octet-stream")},
            data={
                "model": ASR_MODEL,
                "response_format": "verbose_json",
                "diarize": "true" if diarize else "false",
                "language": (asr.get("language") or ASR_LANGUAGE) if language is None else language,
                # normally blank; the smoke suite pins this to get deterministic text
                "backend": os.environ.get("ASR_BACKEND_REQUEST", ""),
                # endpoint chosen on the AI models page, pushed per request
                "asr_base_url": asr.get("base_url", ""),
                "asr_api_key": asr.get("api_key", ""),
                "asr_model": asr.get("model", ""),
                "ca_cert_path": asr.get("ca_cert_path", ""),
                "verify_ssl": "0" if asr.get("verify_ssl") is False else "1",
                # local diarizer (pyannote is gated, so the token travels with the request)
                **diar,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise AudioServiceError(f"ASR service unreachable at {ASR_URL}: {exc}") from exc
    if response.status_code >= 400:
        raise AudioServiceError(f"transcription failed ({response.status_code}): {response.text[:300]}")
    return response.json()


def health():
    out = {}
    for name, url in (("denoise", DENOISE_URL), ("asr", ASR_URL)):
        try:
            response = httpx.get(url.replace("/v1", "") + "/health", timeout=10)
            out[name] = response.json() | {"ok": response.status_code < 400}
        except (httpx.HTTPError, ValueError) as exc:
            out[name] = {"ok": False, "error": str(exc)}
    return out
