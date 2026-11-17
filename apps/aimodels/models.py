import os

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

KINDS = [
    ("llm", "LLM — writes the BRD"),
    ("asr", "ASR — speech to text"),
    ("diarization", "Speaker diarization"),
    ("denoise", "Noise reduction"),
    ("embedding", "Embeddings"),
    ("reranker", "Reranker"),
    ("other", "Other"),
]

PROVIDER_TYPES = [
    ("openai", "OpenAI-compatible"),
    ("internal", "Internal service (this stack)"),
    ("custom", "Custom"),
]


def cert_upload_to(instance, filename):
    return f"certs/{instance.slug or 'provider'}/{filename}"


class ModelProvider(models.Model):
    """An AI/ML endpoint the system can call. Editable from the UI, no redeploy needed."""

    kind = models.CharField(max_length=20, choices=KINDS, default="llm")
    name = models.CharField(max_length=120, help_text="Label shown in the UI")
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    provider_type = models.CharField(max_length=20, choices=PROVIDER_TYPES, default="openai")

    base_url = models.URLField(help_text="e.g. https://host:7879/v1")
    model_name = models.CharField(max_length=200, blank=True, help_text="e.g. Qwen/Qwen3-ASR-0.6B")
    api_key = models.CharField(max_length=500, blank=True, help_text="Sent as: Authorization: Bearer …")

    # TLS
    verify_ssl = models.BooleanField(default=True, help_text="Turn off only for a throwaway test host")
    ca_cert = models.FileField(
        upload_to=cert_upload_to, blank=True, null=True,
        help_text="CA bundle for a private authority, e.g. ezua-domain-ca-cert.crt",
    )
    client_cert = models.FileField(
        upload_to=cert_upload_to, blank=True, null=True, help_text="Optional client certificate (mTLS)"
    )
    client_key = models.FileField(
        upload_to=cert_upload_to, blank=True, null=True, help_text="Optional client private key (mTLS)"
    )

    # call tuning
    timeout = models.PositiveIntegerField(default=300, help_text="Seconds")
    max_tokens = models.PositiveIntegerField(default=8000, blank=True, help_text="Ceiling for the reply")
    context_tokens = models.PositiveIntegerField(
        default=16000,
        help_text="Total window: prompt + reply. Requests are trimmed to fit under it.",
    )
    temperature = models.FloatField(default=0.2)
    language = models.CharField(max_length=20, blank=True, help_text="ASR: blank = auto-detect")
    extra = models.JSONField(default=dict, blank=True, help_text="Extra body fields sent with each request")

    is_default = models.BooleanField(default=False, help_text="Used for this kind of work")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    # last health check
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True)  # ok | error
    last_error = models.TextField(blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    last_models_seen = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "-is_default", "name"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.name}"

    def get_absolute_url(self):
        return reverse("aimodels:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.kind}-{self.name}") or "provider"
            slug, n = base, 1
            while ModelProvider.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)
        if self.is_default:
            ModelProvider.objects.filter(kind=self.kind).exclude(pk=self.pk).update(is_default=False)
        from .runtime import invalidate

        invalidate()

    @property
    def masked_key(self):
        if not self.api_key:
            return "—"
        if len(self.api_key) <= 8:
            return "•" * len(self.api_key)
        return f"{self.api_key[:3]}…{self.api_key[-3:]} ({len(self.api_key)} chars)"

    @property
    def host(self):
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc

    @property
    def uses_tls(self):
        return self.base_url.lower().startswith("https")

    def cert_path(self, field="ca_cert"):
        file = getattr(self, field, None)
        if not file:
            return None
        try:
            path = file.path
        except (NotImplementedError, ValueError):
            return None
        return path if os.path.exists(path) else None

    def httpx_kwargs(self):
        """verify / cert arguments for httpx, honouring the uploaded certificates."""
        kwargs = {}
        ca = self.cert_path("ca_cert")
        if not self.verify_ssl:
            kwargs["verify"] = False
        elif ca:
            kwargs["verify"] = ca
        client_cert = self.cert_path("client_cert")
        client_key = self.cert_path("client_key")
        if client_cert and client_key:
            kwargs["cert"] = (client_cert, client_key)
        elif client_cert:
            kwargs["cert"] = client_cert
        return kwargs

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def as_config(self):
        return {
            "id": self.pk,
            "name": self.name,
            "base_url": self.base_url.rstrip("/"),
            "api_key": self.api_key,
            "model": self.model_name,
            "timeout": float(self.timeout),
            "max_tokens": self.max_tokens,
            "context_tokens": self.context_tokens,
            "temperature": self.temperature,
            "language": self.language,
            "extra": self.extra or {},
            "httpx": self.httpx_kwargs(),
            "source": "database",
        }

    def record_check(self, ok, error="", latency_ms=None, models_seen=None):
        self.last_checked_at = timezone.now()
        self.last_status = "ok" if ok else "error"
        self.last_error = (error or "")[:2000]
        self.last_latency_ms = latency_ms
        self.last_models_seen = models_seen or []
        super().save(
            update_fields=[
                "last_checked_at", "last_status", "last_error", "last_latency_ms",
                "last_models_seen", "updated_at",
            ]
        )
