"""Connection tests for a configured endpoint — used by the Test button."""

import time

import httpx


def probe(provider):
    """-> (ok, message, latency_ms, models_seen). Never raises."""
    started = time.time()
    url = provider.base_url.rstrip("/") + "/models"
    try:
        response = httpx.get(url, headers=provider.headers(), timeout=min(provider.timeout, 30),
                             **provider.httpx_kwargs())
    # a broken uploaded certificate raises ssl.SSLError when the context is built,
    # which is not an httpx error — catch everything so the page never 500s
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000), []

    latency = int((time.time() - started) * 1000)
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}: {response.text[:200]}", latency, []

    try:
        payload = response.json()
        served = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
    except ValueError:
        return True, f"HTTP {response.status_code}, non-JSON reply", latency, []

    if provider.model_name and served and provider.model_name not in served:
        return (
            False,
            f"Reachable, but '{provider.model_name}' is not served. Available: {', '.join(served[:6])}",
            latency,
            served,
        )
    return True, f"OK — {len(served)} model(s) served", latency, served


def generate(provider, prompt="Reply with the single word: ready."):
    """Round-trip a tiny completion so the key and model name are proven, not assumed."""
    started = time.time()
    url = provider.base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32,
        "temperature": 0,
        **(provider.extra or {}),
    }
    try:
        response = httpx.post(url, json=body, headers=provider.headers(),
                              timeout=min(provider.timeout, 60), **provider.httpx_kwargs())
    # a broken uploaded certificate raises ssl.SSLError when the context is built,
    # which is not an httpx error — catch everything so the page never 500s
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000)

    latency = int((time.time() - started) * 1000)
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}: {response.text[:200]}", latency
    try:
        text = response.json()["choices"][0]["message"].get("content", "")
    except (ValueError, KeyError, IndexError):
        return False, f"Unexpected reply: {response.text[:200]}", latency
    return True, f"Replied: {text.strip()[:120] or '(empty)'}", latency
