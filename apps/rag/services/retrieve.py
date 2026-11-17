from pgvector.django import CosineDistance

from ..models import Chunk
from .embed import embed_query
from .rerank import rerank

CANDIDATES = 20
TOP_K = 6


def semantic_search(project, query, top_k=TOP_K):
    """-> list of {label, text, source_type, source_id, score} for this project only,
    nearest by cosine distance then reordered by the cross-encoder reranker."""
    vector = embed_query(query)
    candidates = list(
        Chunk.objects.filter(project=project, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:CANDIDATES]
    )
    if not candidates:
        return []

    try:
        scores = rerank(query, [c.text for c in candidates])
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    except Exception:  # noqa: BLE001 - reranker is an enhancement, not a hard dependency
        ranked = [(c, 1 - c.distance) for c in candidates]

    return [
        {
            "label": c.label, "text": c.text, "source_type": c.source_type,
            "source_id": c.source_id, "score": round(float(score), 4),
        }
        for c, score in ranked[:top_k]
    ]
