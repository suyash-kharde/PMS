from django.conf import settings
from django.db import models
from pgvector.django import VectorField

EMBEDDING_DIMENSIONS = 4096  # Qwen/Qwen3-Embedding-8B

SOURCE_KINDS = [
    ("timeline", "Timeline entry"),
    ("brd", "BRD"),
    ("task", "Task"),
    ("membership", "Person on project"),
    ("customer", "Customer"),
    ("project", "Project"),
]


class Chunk(models.Model):
    """One embedded slice of project text, searched for the chat agent's RAG tool."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="rag_chunks")
    source_type = models.CharField(max_length=20, choices=SOURCE_KINDS)
    source_id = models.PositiveIntegerField()
    ordinal = models.PositiveIntegerField(default=0, help_text="Position within the source, for stable re-indexing")
    label = models.CharField(max_length=200, blank=True, help_text="Human-readable reference, e.g. an entry title")
    text = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "source_type", "source_id", "ordinal"], name="rag_chunk_unique_slot"
            )
        ]
        # no ANN index: pgvector's HNSW/IVFFlat cap out at 2000 dims (halfvec at 4000),
        # under our 4096-dim embeddings. Retrieval is always scoped to one project's
        # chunks (filter first), so a sequential scan over that subset is plenty fast.
        indexes = [models.Index(fields=["project", "source_type", "source_id"])]

    def __str__(self):
        return f"{self.project.key} · {self.source_type}#{self.source_id}.{self.ordinal}"


class ChatSession(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="chat_sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rag_chat_sessions")
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Chat {self.pk}"


class ChatMessage(models.Model):
    ROLES = [("user", "User"), ("assistant", "Assistant")]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLES)
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True, help_text="Chunks cited in this answer")
    sql_used = models.TextField(blank=True, help_text="SQL the database tool ran, for transparency")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
