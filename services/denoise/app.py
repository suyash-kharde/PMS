"""OpenAI-compatible speech-enhancement (noise reduction) service.

Endpoints
    GET  /health
    GET  /v1/models
    POST /v1/audio/enhancements     multipart: file, [model], [response_format=wav|json]

Engine: weya-ai/hush (DeepFilterNet-style speech enhancement, 16 kHz mono).
Set ENGINE=hush|fallback|auto. `auto` uses hush when the weights and DeepFilterLib
import cleanly, otherwise a spectral-gating fallback so the pipeline still runs.
"""

import io
import os
import sys
import time
import wave

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

MODEL_ID = os.environ.get("DENOISE_MODEL", "weya-ai/hush")
ENGINE = os.environ.get("ENGINE", "auto")
# auto -> CUDA when torch sees a GPU, otherwise CPU. cuda / cpu force one.
# Any CUDA error at load or inference downgrades this to CPU for the rest of the run.
DENOISE_DEVICE = os.environ.get("DENOISE_DEVICE", "auto").lower()
SAMPLE_RATE = 16000

app = FastAPI(title="hush-denoise", version="1.1")
_state = {"engine": None, "model": None, "df": None, "error": None, "device": "cpu"}
_force_cpu = False


def pick_device() -> str:
    """'cuda' only when asked (or auto) AND torch can actually see a working GPU."""
    if _force_cpu or DENOISE_DEVICE == "cpu":
        return "cpu"
    try:
        import torch

        if DENOISE_DEVICE in ("cuda", "auto") and torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 - no torch / broken CUDA -> CPU
        pass
    return "cpu"


def _downgrade_to_cpu(reason: str):
    """A CUDA failure must not take the service down — drop to CPU and reload lazily."""
    global _force_cpu
    _force_cpu = True
    _state["error"] = f"CUDA disabled ({reason}); using CPU"
    _state["model"] = _state["df"] = _state["engine"] = None
    _state["device"] = "cpu"


# --------------------------------------------------------------------------- audio io
def decode_audio(raw: bytes, filename: str) -> tuple[np.ndarray, int]:
    """Return mono float32 in [-1,1] at 16 kHz. Uses ffmpeg for anything but plain wav."""
    try:
        with wave.open(io.BytesIO(raw)) as wav:
            if wav.getsampwidth() == 2:
                frames = wav.readframes(wav.getnframes())
                data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                if wav.getnchannels() > 1:
                    data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
                if wav.getframerate() == SAMPLE_RATE:
                    return data, SAMPLE_RATE
                return resample(data, wav.getframerate(), SAMPLE_RATE), SAMPLE_RATE
    except (wave.Error, EOFError):
        pass
    return decode_with_ffmpeg(raw, filename), SAMPLE_RATE


def decode_with_ffmpeg(raw: bytes, filename: str) -> np.ndarray:
    import subprocess
    import tempfile

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


def resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or data.size == 0:
        return data
    n = int(round(data.size * dst_rate / src_rate))
    return np.interp(np.linspace(0, data.size - 1, n), np.arange(data.size), data).astype(np.float32)


def encode_wav(data: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    clipped = np.clip(data, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


# --------------------------------------------------------------------------- engines
def load_hush():
    """Fetch the weya-ai/hush snapshot and import its DfNetSE implementation."""
    import torch
    from huggingface_hub import snapshot_download

    local = snapshot_download(MODEL_ID, cache_dir=os.environ.get("HF_HOME", "/models"))
    if local not in sys.path:
        sys.path.insert(0, local)

    from libdf import DF  # noqa: F401  (DeepFilterLib — required by hush)
    from model.dfnet_se import DfNetSE, get_config

    config = get_config()
    model = DfNetSE(config)
    checkpoint = None
    for name in ("model_best.ckpt", "pytorch_model.bin", "model.ckpt"):
        candidate = os.path.join(local, name)
        if os.path.exists(candidate):
            checkpoint = torch.load(candidate, map_location="cpu")
            break
    if checkpoint is None:
        raise RuntimeError("no hush checkpoint found in the snapshot")
    state = checkpoint.get("state_dict", checkpoint)
    model.model.load_state_dict(state)
    model.eval()

    device = pick_device()
    if device == "cuda":
        try:
            model = model.to("cuda")
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the load
            _downgrade_to_cpu(f"{type(exc).__name__}: {exc}")
            device = "cpu"
    _state["device"] = device

    df = DF(sr=SAMPLE_RATE, fft_size=960, hop_size=480, nb_bands=32)
    return model, df


def enhance_hush(data: np.ndarray) -> np.ndarray:
    import torch
    from libdf import erb, erb_norm, unit_norm

    model, df = _state["model"], _state["df"]
    device = _state.get("device", "cpu")
    wav = torch.tensor(data, dtype=torch.float32).unsqueeze(0)
    try:
        with torch.no_grad():
            # DeepFilterLib analysis/synthesis is numpy on CPU; only the net runs on the GPU.
            spec = df.analysis(wav.numpy())
            erb_feat = torch.as_tensor(erb_norm(erb(spec, 32), alpha=0.99)).unsqueeze(0).unsqueeze(0)
            spec_t = torch.as_tensor(spec).unsqueeze(0)
            spec_feat = torch.view_as_real(spec_t)
            cplx_feat = torch.as_tensor(unit_norm(spec[..., :96], alpha=0.99)).unsqueeze(0)
            cplx_feat = torch.view_as_real(cplx_feat)
            if device == "cuda":
                spec_feat = spec_feat.to("cuda")
                erb_feat = erb_feat.to("cuda")
                cplx_feat = cplx_feat.to("cuda")
            enhanced_spec = model(spec_feat, erb_feat, cplx_feat)[0].detach().cpu()
            enhanced = df.synthesis(torch.view_as_complex(enhanced_spec.squeeze(0)).numpy())
        return np.asarray(enhanced).reshape(-1)
    except Exception as exc:  # noqa: BLE001
        if device == "cuda":
            # a CUDA-side failure: drop to CPU and retry this request once on CPU
            _downgrade_to_cpu(f"{type(exc).__name__}: {exc}")
            _state["model"], _state["df"] = load_hush()
            _state["engine"] = "hush"
            return enhance_hush(data)
        raise


def enhance_fallback(data: np.ndarray) -> np.ndarray:
    """Spectral gating: estimate the noise floor from the quietest frames and subtract it."""
    if data.size < 2048:
        return data
    frame, hop = 1024, 256
    window = np.hanning(frame).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(data, frame)[::hop] * window
    spec = np.fft.rfft(frames, axis=1)
    magnitude, phase = np.abs(spec), np.angle(spec)
    noise = np.quantile(magnitude, 0.1, axis=0, keepdims=True)
    cleaned = np.maximum(magnitude - 1.6 * noise, 0.08 * magnitude)
    rebuilt = np.fft.irfft(cleaned * np.exp(1j * phase), axis=1) * window

    out = np.zeros(data.size + frame, dtype=np.float32)
    norm = np.zeros_like(out)
    for i, chunk in enumerate(rebuilt):
        start = i * hop
        out[start:start + frame] += chunk
        norm[start:start + frame] += window ** 2
    out = out[: data.size] / np.maximum(norm[: data.size], 1e-6)
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * min(1.0, np.max(np.abs(data)) or 1.0)).astype(np.float32)


def ensure_engine() -> str:
    if _state["engine"]:
        return _state["engine"]
    if ENGINE in ("auto", "hush"):
        try:
            _state["model"], _state["df"] = load_hush()
            _state["engine"] = "hush"
            return "hush"
        except Exception as exc:  # noqa: BLE001 - degrade instead of failing the pipeline
            _state["error"] = f"{type(exc).__name__}: {exc}"
            if ENGINE == "hush":
                raise HTTPException(503, f"hush unavailable: {_state['error']}")
    _state["engine"] = "fallback"
    return "fallback"


# --------------------------------------------------------------------------- api
@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": _state["engine"] or ENGINE,
        "device": _state.get("device", "cpu"),
        "device_requested": DENOISE_DEVICE,
        "load_error": _state["error"],
    }


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "weya-ai", "created": 0}],
    }


@app.post("/v1/audio/enhancements")
async def enhance(
    file: UploadFile = File(...),
    model: str = Form(MODEL_ID),
    response_format: str = Form("wav"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    started = time.time()
    data, rate = decode_audio(raw, file.filename or "audio.wav")
    engine = ensure_engine()
    try:
        cleaned = enhance_hush(data) if engine == "hush" else enhance_fallback(data)
    except Exception as exc:  # noqa: BLE001
        _state["error"] = f"{type(exc).__name__}: {exc}"
        _state["engine"] = "fallback"
        engine, cleaned = "fallback", enhance_fallback(data)

    wav_bytes = encode_wav(cleaned, rate)
    meta = {
        "model": model,
        "engine": engine,
        "sample_rate": rate,
        "duration": round(len(data) / rate, 3),
        "processing_seconds": round(time.time() - started, 3),
    }
    if response_format == "json":
        import base64

        return JSONResponse({**meta, "audio": base64.b64encode(wav_bytes).decode()})
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"x-engine": engine, "x-duration": str(meta["duration"])},
    )
