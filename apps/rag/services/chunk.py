"""Plain word-based chunking. Good enough for prose notes, transcripts and BRD text —
no sentence tokenizer dependency, just a sliding window over whitespace-split words."""


def chunk_text(text, size=220, overlap=40):
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [text.strip()]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return chunks
