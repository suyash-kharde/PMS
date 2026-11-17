from django import forms

from .models import ModelProvider

CERT_SUFFIXES = (".crt", ".pem", ".cer", ".ca-bundle", ".key")
MAX_CERT_BYTES = 512 * 1024


class ModelProviderForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password",
                                                             "placeholder": "leave blank to keep the stored key"}),
        help_text="Stored server-side and never shown again",
    )

    accept_insecure = forms.BooleanField(
        required=False,
        label="I accept an unverified TLS connection",
        help_text="Only needed when certificate checking is off for an HTTPS endpoint",
    )

    class Meta:
        model = ModelProvider
        fields = [
            "kind", "name", "provider_type", "base_url", "model_name", "api_key",
            "verify_ssl", "ca_cert", "client_cert", "client_key",
            "timeout", "max_tokens", "context_tokens", "temperature", "language", "extra",
            "is_default", "is_active", "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "extra": forms.Textarea(attrs={"rows": 2, "placeholder": '{"top_p": 0.9}'}),
            "base_url": forms.URLInput(attrs={"placeholder": "https://host:port/v1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.api_key:
            self.fields["api_key"].help_text = (
                f"A key is stored ({self.instance.masked_key}). Leave blank to keep it."
            )

    def clean_base_url(self):
        url = (self.cleaned_data.get("base_url") or "").strip().rstrip("/")
        if url.endswith("/chat/completions") or url.endswith("/audio/transcriptions"):
            raise forms.ValidationError("Give the base URL only — it ends at /v1.")
        return url

    def _check_cert(self, field):
        file = self.cleaned_data.get(field)
        if not file or not getattr(file, "name", ""):
            return file
        name = file.name.lower()
        if not name.endswith(CERT_SUFFIXES):
            raise forms.ValidationError(f"Expected one of {', '.join(CERT_SUFFIXES)} — got “{file.name}”.")
        if file.size > MAX_CERT_BYTES:
            raise forms.ValidationError("That file is too large for a certificate.")
        if not hasattr(file, "read"):
            return file
        blob = file.read()
        file.seek(0)
        if b"-----BEGIN" not in blob[:200]:
            raise forms.ValidationError("That does not look like a PEM file (no -----BEGIN header).")
        if field != "client_key":
            # parse it now: a malformed certificate would otherwise fail on every call
            try:
                from cryptography.hazmat.primitives.serialization import load_pem_public_key  # noqa: F401
                from cryptography.x509 import load_pem_x509_certificate

                load_pem_x509_certificate(blob[: blob.rfind(b"-----END") + 40] if b"-----END" in blob else blob)
            except Exception as exc:  # noqa: BLE001
                raise forms.ValidationError(f"That PEM file could not be parsed as a certificate: {exc}") from exc
        return file

    def clean_ca_cert(self):
        return self._check_cert("ca_cert")

    def clean_client_cert(self):
        return self._check_cert("client_cert")

    def clean_client_key(self):
        return self._check_cert("client_key")

    def clean_extra(self):
        extra = self.cleaned_data.get("extra")
        if extra in (None, "", {}):
            return {}
        if not isinstance(extra, dict):
            raise forms.ValidationError("Extra must be a JSON object, e.g. {\"top_p\": 0.9}")
        return extra

    def clean(self):
        data = super().clean()
        https = data.get("base_url", "").lower().startswith("https")
        if https and not data.get("verify_ssl") and not data.get("accept_insecure"):
            self.add_error(
                "accept_insecure",
                "Turning off certificate checking exposes this connection to interception. "
                "Upload the CA certificate instead if you have it — or tick this box to accept the risk.",
            )
        return data

    def save(self, commit=True):
        provider = super().save(commit=False)
        # a blank key means "keep what is stored", not "delete it"
        if not self.cleaned_data.get("api_key") and self.instance.pk:
            provider.api_key = ModelProvider.objects.get(pk=self.instance.pk).api_key
        if commit:
            provider.save()
        return provider
