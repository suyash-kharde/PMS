"""Calls the reranker provider configured in AI models -> Reranker.
This deployment's endpoint is a custom pairwise /v1/score, not the OpenAI-style
/v1/rerank — one query (text_1) scored against a batch of candidates (text_2)."""

import httpx

from apps.aimodels import runtime


def _headers(cfg):
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return headers


def rerank(query, documents):
    """-> list[float] scores, same order as `documents`. Raises on failure."""
    if not documents:
        return []
    cfg = runtime.config_for("reranker")
    if not cfg.get("base_url"):
        raise RuntimeError("No reranker provider configured (AI models -> Reranker).")

    url = cfg["base_url"].rstrip("/") + "/score"
    response = httpx.post(
        url, json={"model": cfg["model"], "text_1": query, "text_2": documents}, headers=_headers(cfg),
        timeout=cfg.get("timeout", 60), **cfg.get("httpx", {}),
    )
    response.raise_for_status()
    payload = response.json()
    by_index = {row["index"]: row["score"] for row in payload["data"]}
    return [by_index[i] for i in range(len(documents))]
