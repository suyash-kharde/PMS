"""Calls the embedding provider configured in AI models -> Embeddings."""

import httpx

from apps.aimodels import runtime


def _headers(cfg):
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


def embed_texts(texts):
    """-> list[list[float]] in the same order as `texts`. Raises on failure —
    callers decide whether a partial index is acceptable."""
    if not texts:
        return []
    cfg = runtime.config_for("embedding")
    if not cfg.get("base_url"):
        raise RuntimeError("No embedding provider configured (AI models -> Embeddings).")

    url = cfg["base_url"].rstrip("/") + "/embeddings"
    response = httpx.post(
        url, json={"model": cfg["model"], "input": texts}, headers=_headers(cfg),
        timeout=cfg.get("timeout", 60), **cfg.get("httpx", {}),
    )
    response.raise_for_status()
    payload = response.json()
    by_index = {row["index"]: row["embedding"] for row in payload["data"]}
    return [by_index[i] for i in range(len(texts))]


def embed_query(text):
    return embed_texts([text])[0]
