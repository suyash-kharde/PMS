"""Create the endpoint rows the stack ships with (safe to re-run).

    python manage.py seed_providers

Rows are only created when missing — an endpoint edited in the UI is never overwritten.
Upload the CA certificate on the AI models page for the HTTPS ones.
"""

from django.core.management.base import BaseCommand

from apps.aimodels.models import ModelProvider
from apps.aimodels.runtime import invalidate

PROVIDERS = [
    {
        "kind": "llm",
        "name": "Qwen3.6 35B (orbitindia)",
        "base_url": "https://multimodel-qwen36.otw-aiapp.orbitindia.in/v1",
        "model_name": "RedHatAI/Qwen3.6-35B-A3B-NVFP4",
        "api_key": "cant-be-empty",
        "timeout": 300,
        "max_tokens": 8000,
        "temperature": 0.2,
        "is_default": True,
        "notes": "Writes the BRD. Private CA — upload ezua-domain-ca-cert.crt as the CA cert.",
    },
    {
        "kind": "asr",
        "name": "Qwen3-ASR 0.6B (orbitindia)",
        "base_url": "https://multimodel-asr.otw-aiapp.orbitindia.in/v1",
        "model_name": "Qwen/Qwen3-ASR-0.6B",
        "api_key": "cant-be-empty",
        "timeout": 900,
        "is_default": True,
        "notes": "Multi-language speech to text, one call per speaker turn. Private CA — upload the CA cert.",
    },
    {
        "kind": "llm",
        "name": "Qwen3.6 35B (direct IP)",
        "base_url": "http://172.16.15.111:7879/v1",
        "model_name": "RedHatAI/Qwen3.6-35B-A3B-NVFP4",
        "api_key": "ollama",
        "timeout": 300,
        "max_tokens": 8000,
        "temperature": 0.2,
        "is_default": False,
        "notes": "Plain HTTP fallback on the internal network.",
    },
    {
        "kind": "asr",
        "name": "Qwen3-ASR 0.6B (direct IP)",
        "base_url": "http://172.16.15.111:7600/v1",
        "model_name": "Qwen/Qwen3-ASR-0.6B",
        "api_key": "cant-be-empty",
        "timeout": 900,
        "is_default": False,
        "notes": "Plain HTTP fallback on the internal network.",
    },
    {
        "kind": "diarization",
        "name": "pyannote speaker-diarization 3.1 (local)",
        "provider_type": "internal",
        "base_url": "http://asr:8200/v1",
        "model_name": "pyannote/speaker-diarization-3.1",
        "timeout": 900,
        "is_default": True,
        "notes": (
            "The only model that runs locally. Gated on Hugging Face: accept the conditions on "
            "pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0, then paste a read "
            "token as the API key here. Optional extra: "
            '{"min_speakers": 2, "max_speakers": 4} to bound the speaker count.'
        ),
    },
    {
        "kind": "denoise",
        "name": "hush (in-stack)",
        "provider_type": "internal",
        "base_url": "http://denoise:8100/v1",
        "model_name": "weya-ai/hush",
        "timeout": 900,
        "is_default": True,
        "notes": "Noise reduction before transcription; needs INSTALL_HUSH=1 for the real weights.",
    },
]


class Command(BaseCommand):
    help = "Seed the AI model endpoints that ship with this stack."

    def handle(self, *args, **options):
        created = 0
        for spec in PROVIDERS:
            spec = dict(spec)
            name = spec.pop("name")
            provider, made = ModelProvider.objects.get_or_create(name=name, defaults=spec)
            if made:
                created += 1
                self.stdout.write(f"  + {provider.get_kind_display()}: {name}")
        invalidate()
        self.stdout.write(self.style.SUCCESS(f"{created} endpoint(s) created, {ModelProvider.objects.count()} total"))
