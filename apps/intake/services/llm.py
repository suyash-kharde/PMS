"""Thin OpenAI-compatible chat client (no SDK dependency).

The endpoint, key, model and TLS material come from the AI models page
(apps.aimodels), falling back to the LLM_* environment variables.
"""

import httpx

from apps.aimodels.runtime import config_for


class LLMError(RuntimeError):
    pass


def settings():
    return config_for("llm")


# kept as module attributes for the status pages and existing imports
def __getattr__(name):  # pragma: no cover - convenience accessors
    mapping = {"BASE_URL": "base_url", "MODEL": "model", "API_KEY": "api_key", "TIMEOUT": "timeout"}
    if name in mapping:
        return settings()[mapping[name]]
    raise AttributeError(name)


def chat(messages, model=None, temperature=None, max_tokens=None, timeout=None):
    """Return (text, meta). Raises LLMError on transport or protocol failure."""
    config = settings()
    payload = {
        "model": model or config["model"],
        "messages": messages,
        "temperature": config["temperature"] if temperature is None else temperature,
        "max_tokens": max_tokens or config["max_tokens"] or 8000,
        "stream": False,
        **(config.get("extra") or {}),
    }
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"

    try:
        response = httpx.post(
            f"{config['base_url']}/chat/completions",
            json=payload,
            headers=headers,
            timeout=timeout or config["timeout"],
            **config.get("httpx", {}),
        )
    except httpx.HTTPError as exc:
        raise LLMError(f"cannot reach the LLM at {config['base_url']}: {exc}") from exc

    if response.status_code >= 400:
        raise LLMError(f"LLM returned {response.status_code}: {response.text[:400]}")

    try:
        data = response.json()
        choice = data["choices"][0]["message"]
        text = choice.get("content") or ""
        if not text and choice.get("reasoning_content"):
            text = choice["reasoning_content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LLMError(f"unexpected LLM response: {response.text[:400]}") from exc

    return strip_reasoning(text), {
        "model": data.get("model", payload["model"]),
        "usage": data.get("usage", {}),
        "endpoint": config["base_url"],
        "provider": config.get("name", ""),
    }


def strip_reasoning(text: str) -> str:
    """Qwen-style models may wrap their scratchpad in <think>…</think>."""
    while "<think>" in text and "</think>" in text:
        head, _, rest = text.partition("<think>")
        _, _, tail = rest.partition("</think>")
        text = head + tail
    return text.strip()


def health():
    config = settings()
    headers = {"Authorization": f"Bearer {config['api_key']}"} if config.get("api_key") else {}
    try:
        response = httpx.get(
            f"{config['base_url']}/models", headers=headers, timeout=10, **config.get("httpx", {})
        )
        return {
            "ok": response.status_code < 400,
            "status": response.status_code,
            "endpoint": config["base_url"],
            "provider": config.get("name", ""),
            "source": config.get("source", ""),
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False, "error": str(exc), "endpoint": config["base_url"],
            "provider": config.get("name", ""), "source": config.get("source", ""),
        }
