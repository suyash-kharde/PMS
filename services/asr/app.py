"""OpenAI-compatible ASR + speaker-diarization service.

Two models, two jobs:
  * diarization — pyannote/speaker-diarization-3.1, the only model that runs locally.
    It decides *who spoke when*; it never transcribes.
  * ASR — a remote OpenAI-compatible endpoint (Qwen/Qwen3-ASR-0.6B on vLLM) for
    multi-language speech-to-text. No ASR weights are stored in this container.

pyannote 3.1 is gated on Hugging Face: accept the conditions on both
`pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, then supply a read
token as HF_TOKEN, or as the API key of the diarization endpoint on the AI models page.

That endpoint only returns `{"text": ...}` (it rejects verbose_json), so there are no
word timestamps to align against. Instead the pipeline is:

    diarize -> slice the audio on speaker turns -> transcribe each slice -> reassemble

which gives per-speaker text with real timings, and keeps every request short enough for
the model's context.

Endpoints
    GET  /health
    GET  /v1/models
    POST /v1/audio/transcriptions   multipart: file, [model], [response_format], [diarize], [language]
"""

import io
import os
import tempfile
import time
import wave

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

ASR_MODEL = os.environ.get("ASR_MODEL", "Qwen/Qwen3-ASR-0.6B")
ASR_BASE_URL = os.environ.get("ASR_BASE_URL", "http://172.16.15.111:7600/v1").rstrip("/")
ASR_API_KEY = os.environ.get("ASR_API_KEY", "cant-be-empty")
ASR_BACKEND = os.environ.get("ASR_BACKEND", "auto")  # auto | remote | mock
ASR_TIMEOUT = float(os.environ.get("ASR_TIMEOUT", "180"))

DIAR_MODEL = os.environ.get("DIAR_MODEL", "pyannote/speaker-diarization-3.1")
DIARIZER = os.environ.get("DIARIZER", os.environ.get("ENGINE", "auto"))  # auto | pyannote | mock
DIAR_DEVICE = os.environ.get("DIAR_DEVICE", "auto")  # auto | cuda | cpu
HF_TOKEN = os.environ.get("HF_TOKEN", "")

MAX_CHUNK_SECONDS = float(os.environ.get("MAX_CHUNK_SECONDS", "25"))
MIN_CHUNK_SECONDS = float(os.environ.get("MIN_CHUNK_SECONDS", "0.4"))
# a long recording is many short calls; run a few at once and reuse the connection
ASR_CONCURRENCY = int(os.environ.get("ASR_CONCURRENCY", "4"))
# after a failure, wait this long before trying the remote endpoint again
ASR_RETRY_COOLDOWN = float(os.environ.get("ASR_RETRY_COOLDOWN", "60"))
SAMPLE_RATE = 16000

app = FastAPI(title="parakeet-diarization + qwen-asr", version="2.1")
_state = {
    "diarizer": None, "diar_model": None, "asr_ok": None, "diar_device": "—",
    "diar_error": None, "diar_device_note": "", "asr_error": None, "asr_failed_at": 0.0,
}


# --------------------------------------------------------------------------- audio helpers
def to_pcm16k(raw: bytes, filename: str) -> np.ndarray:
    """Decode anything ffmpeg understands into mono float32 at 16 kHz."""
    import subprocess

    suffix = os.path.splitext(filename or "")[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
        src.write(raw)
        path = src.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise HTTPException(400, f"Could not decode audio: {proc.stderr.decode()[:300]}")
        return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        os.unlink(path)


def encode_wav(samples: np.ndarray) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def write_temp_wav(samples: np.ndarray) -> str:
    path = tempfile.mktemp(suffix=".wav")
    with open(path, "wb") as handle:
        handle.write(encode_wav(samples))
    return path


# --------------------------------------------------------------------------- diarization (NeMo)
def normalise_label(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return f"SPEAKER_{int(digits or 0):02d}"


def load_diarizer(token=""):
    """pyannote/speaker-diarization-3.1, on the GPU when there is one."""
    import torch
    from pyannote.audio import Pipeline

    # pyannote.audio 4.x removed `use_auth_token=`; it is `token=` now.
    pipeline = Pipeline.from_pretrained(DIAR_MODEL, token=token or HF_TOKEN or None)
    if pipeline is None:
        # pyannote returns None instead of raising when the gate has not been accepted
        raise RuntimeError(
            f"{DIAR_MODEL} did not load. Accept the conditions on the model page and on "
            "pyannote/segmentation-3.0, then supply a read token (HF_TOKEN or the API key "
            "of the diarization endpoint)."
        )
    want_cuda = DIAR_DEVICE in ("cuda", "auto")
    has_cuda = False
    try:
        has_cuda = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - broken CUDA userspace reads as "no GPU"
        has_cuda = False
    _state["diar_device_note"] = ""
    if want_cuda and has_cuda:
        try:
            pipeline.to(torch.device("cuda"))
            _state["diar_device"] = "cuda"
        except Exception as exc:  # noqa: BLE001 - CPU is always an acceptable fallback
            _state["diar_device"] = "cpu"
            _state["diar_device_note"] = f"CUDA unavailable, using CPU: {type(exc).__name__}: {exc}"[:300]
    else:
        _state["diar_device"] = "cpu"
        if DIAR_DEVICE == "cuda" and not has_cuda:
            _state["diar_device_note"] = "DIAR_DEVICE=cuda but no GPU visible; using CPU"
    return pipeline, DIAR_MODEL


def ensure_diarizer(token="") -> str:
    if _state["diarizer"] is not None:
        return "pyannote"
    if _state["diar_error"] and DIARIZER == "auto" and not token:
        return "mock"
    if DIARIZER in ("auto", "pyannote"):
        try:
            _state["diarizer"], _state["diar_model"] = load_diarizer(token)
            _state["diar_error"] = None
            return "pyannote"
        except Exception as exc:  # noqa: BLE001
            _state["diar_error"] = f"{type(exc).__name__}: {exc}"[:500]
            if DIARIZER == "pyannote":
                raise HTTPException(503, f"diarizer unavailable: {_state['diar_error']}")
    return "mock"


def diarize_pyannote(path: str, num_speakers=None, min_speakers=None, max_speakers=None):
    """-> [(start, end, 'SPEAKER_XX')] sorted by start."""
    import torch

    kwargs = {}
    if num_speakers:
        kwargs["num_speakers"] = int(num_speakers)
    else:
        if min_speakers:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers:
            kwargs["max_speakers"] = int(max_speakers)

    with torch.no_grad():
        annotation = _state["diarizer"](path, **kwargs)

    segments = [
        (float(turn.start), float(turn.end), normalise_label(speaker))
        for turn, _track, speaker in annotation.itertracks(yield_label=True)
    ]
    return merge_adjacent(sorted(segments))


def merge_adjacent(segments, gap=0.4):
    """pyannote emits many short turns; glue neighbouring ones from the same speaker."""
    merged = []
    for start, end, label in segments:
        if merged and merged[-1][2] == label and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], end, label)
        else:
            merged.append((start, end, label))
    return merged


def diarize_mock(samples: np.ndarray, speakers=2):
    """Even turns over the recording — enough to exercise the slice/ASR/assemble path."""
    total = len(samples) / SAMPLE_RATE
    if total <= 0:
        return []
    turn = max(2.0, min(MAX_CHUNK_SECONDS, total / 4))
    segments, cursor, index = [], 0.0, 0
    while cursor < total:
        end = min(total, cursor + turn)
        segments.append((cursor, end, f"SPEAKER_{index % speakers:02d}"))
        cursor, index = end, index + 1
    return segments


def single_speaker(samples: np.ndarray):
    return [(0.0, len(samples) / SAMPLE_RATE, "SPEAKER_00")]


def split_long(segments):
    """Keep every slice inside the ASR model's comfortable context."""
    out = []
    for start, end, label in segments:
        length = end - start
        if length <= MAX_CHUNK_SECONDS:
            out.append((start, end, label))
            continue
        pieces = int(np.ceil(length / MAX_CHUNK_SECONDS))
        step = length / pieces
        for i in range(pieces):
            out.append((start + i * step, min(end, start + (i + 1) * step), label))
    return out


# --------------------------------------------------------------------------- ASR (remote Qwen)
def build_client(override=None):
    """One httpx client per request: TLS material from the AI models page, keep-alive on."""
    import httpx

    override = override or {}
    tls = {}
    ca_path = override.get("ca_cert_path")
    if override.get("verify_ssl") == "0":
        tls["verify"] = False
    elif ca_path and os.path.exists(ca_path):
        tls["verify"] = ca_path
    return httpx.Client(
        timeout=ASR_TIMEOUT,
        limits=httpx.Limits(max_connections=max(2, ASR_CONCURRENCY), max_keepalive_connections=ASR_CONCURRENCY),
        **tls,
    )


def transcribe_remote(client, samples: np.ndarray, language: str = "", override=None) -> str:
    """One slice -> text, via the OpenAI-compatible endpoint. Multi-language.

    `override` carries per-request endpoint/key/model settings pushed by the application's
    AI models page, so the endpoint can be changed without a redeploy.
    """
    override = override or {}
    base_url = (override.get("base_url") or ASR_BASE_URL).rstrip("/")
    api_key = override.get("api_key") or ASR_API_KEY
    model = override.get("model") or ASR_MODEL

    data = {"model": model, "response_format": "json"}
    if language and language != "auto":
        data["language"] = language
    response = client.post(
        f"{base_url}/audio/transcriptions",
        files={"file": ("chunk.wav", encode_wav(samples), "audio/wav")},
        data=data,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code}: {response.text[:200]}")
    payload = response.json()
    return (payload.get("text") or "").strip()


def asr_backend() -> str:
    if ASR_BACKEND == "mock":
        return "mock"
    if ASR_BACKEND == "auto" and _state["asr_ok"] is False:
        # do not stay on mock forever because the endpoint was down once
        if time.time() - _state["asr_failed_at"] < ASR_RETRY_COOLDOWN:
            return "mock"
    return "remote"


def transcribe_slice(client, samples: np.ndarray, language: str, backend: str, index: int, override=None) -> str:
    if backend == "remote":
        try:
            text = transcribe_remote(client, samples, language, override)
            _state["asr_ok"] = True
            return text
        except Exception as exc:  # noqa: BLE001
            _state["asr_error"] = f"{type(exc).__name__}: {exc}"[:300]
            _state["asr_failed_at"] = time.time()
            if ASR_BACKEND == "remote":
                raise HTTPException(502, f"ASR endpoint failed: {_state['asr_error']}")
            _state["asr_ok"] = False
    seconds = len(samples) / SAMPLE_RATE
    rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    return (
        f"[mock transcript {index + 1} · {seconds:.1f}s · rms {rms:.3f}] "
        f"remote ASR unavailable ({_state['asr_error'] or 'not configured'})."
    )


# --------------------------------------------------------------------------- api
@app.get("/health")
def health():
    return {
        "status": "ok",
        "asr": {
            "model": ASR_MODEL,
            "endpoint": ASR_BASE_URL,
            "backend": ASR_BACKEND,
            "reachable": _state["asr_ok"],
            "error": _state["asr_error"],
        },
        "diarization": {
            "model": DIAR_MODEL,
            "engine": "pyannote" if _state["diarizer"] is not None else DIARIZER,
            "device": _state.get("diar_device", "—"),
            "device_requested": DIAR_DEVICE,
            "device_note": _state.get("diar_device_note", ""),
            "token": "set" if HF_TOKEN else "not set",
            "error": _state["diar_error"],
        },
        # kept for the existing dashboard card
        "engine": ("pyannote" if _state["diarizer"] is not None else "mock")
                  + "+" + ("qwen" if _state["asr_ok"] else asr_backend()),
        "load_error": _state["diar_error"],
    }


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {"id": ASR_MODEL, "object": "model", "owned_by": "qwen", "created": 0, "role": "asr"},
            {"id": DIAR_MODEL, "object": "model", "owned_by": "pyannote", "created": 0, "role": "diarization"},
        ],
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(ASR_MODEL),
    response_format: str = Form("verbose_json"),
    diarize: str = Form("true"),
    language: str = Form(""),
    backend: str = Form(""),  # per-request override of ASR_BACKEND: remote | mock
    # pushed by the application's AI models page
    asr_base_url: str = Form(""),
    asr_api_key: str = Form(""),
    asr_model: str = Form(""),
    ca_cert_path: str = Form(""),
    verify_ssl: str = Form("1"),
    # diarization controls
    hf_token: str = Form(""),
    num_speakers: str = Form(""),
    min_speakers: str = Form(""),
    max_speakers: str = Form(""),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")

    started = time.time()
    samples = to_pcm16k(raw, file.filename or "audio.wav")
    duration = len(samples) / SAMPLE_RATE
    want_diarization = str(diarize).lower() in ("1", "true", "yes")

    # 1. who spoke when
    diar_engine = "none"
    if want_diarization:
        diar_engine = ensure_diarizer(hf_token)
        if diar_engine == "pyannote":
            path = write_temp_wav(samples)
            try:
                turns = diarize_pyannote(path, num_speakers, min_speakers, max_speakers) or single_speaker(samples)
            except Exception as exc:  # noqa: BLE001
                _state["diar_error"] = f"{type(exc).__name__}: {exc}"[:500]
                _state["diarizer"] = None
                if DIARIZER == "pyannote":
                    raise HTTPException(503, f"diarization failed: {_state['diar_error']}")
                diar_engine = "mock"
                turns = diarize_mock(samples)
            finally:
                os.unlink(path)
        else:
            turns = diarize_mock(samples)
    else:
        turns = single_speaker(samples)

    turns = [t for t in split_long(turns) if (t[1] - t[0]) >= MIN_CHUNK_SECONDS] or single_speaker(samples)

    # 2. transcribe every turn on its own, then reassemble
    backend = backend if backend in ("remote", "mock") else asr_backend()
    override = {
        "base_url": asr_base_url, "api_key": asr_api_key, "model": asr_model,
        "ca_cert_path": ca_cert_path, "verify_ssl": verify_ssl,
    }
    from concurrent.futures import ThreadPoolExecutor

    pieces = [
        (index, start, end, label, samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
        for index, (start, end, label) in enumerate(turns)
    ]
    pieces = [p for p in pieces if p[4].size]

    client = build_client(override)
    try:
        if backend == "remote" and ASR_CONCURRENCY > 1 and len(pieces) > 1:
            with ThreadPoolExecutor(max_workers=ASR_CONCURRENCY) as pool:
                texts = list(pool.map(
                    lambda p: transcribe_slice(client, p[4], language, backend, p[0], override), pieces
                ))
        else:
            texts = [transcribe_slice(client, p[4], language, backend, p[0], override) for p in pieces]
    finally:
        client.close()

    segments = []
    for (index, start, end, label, _chunk), text in zip(pieces, texts):
        if not text:
            continue  # silence or music: drop the empty turn
        segments.append(
            {"id": len(segments), "start": round(start, 2), "end": round(end, 2),
             "speaker": label, "text": text}
        )

    used_backend = "qwen-remote" if (backend == "remote" and _state["asr_ok"]) else "mock"
    payload = {
        "task": "transcribe",
        "language": language or "auto",
        "duration": round(duration, 2),
        "text": " ".join(s["text"] for s in segments),
        "model": model,
        "engine": f"{diar_engine}+{used_backend}",
        "asr_model": asr_model or ASR_MODEL,
        "asr_engine": used_backend,
        "diarization_model": DIAR_MODEL if diar_engine == "nemo" else "mock",
        "diarization_engine": diar_engine,
        "turns": len(turns),
        "processing_seconds": round(time.time() - started, 2),
        "speakers": sorted({s["speaker"] for s in segments}),
    }
    if response_format == "text":
        return payload["text"]
    if response_format == "verbose_json":
        payload["segments"] = segments
    return payload
