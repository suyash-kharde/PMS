from django.conf import settings
from django.db import models

KINDS = [
    ("customers", "Customers"),
    ("projects", "Projects"),
    ("people", "People"),
    ("combined", "All three"),
]
STATUSES = [
    ("pending", "Pending review"),
    ("committed", "Committed"),
]


class ImportBatch(models.Model):
    """An uploaded bulk-import sheet. The file itself stays the source of truth —
    it's re-validated fresh both on review and on commit, so nothing stale is cached."""

    kind = models.CharField(max_length=12, choices=KINDS)
    file = models.FileField(upload_to="bulk_imports/%Y/%m/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bulk_imports")
    status = models.CharField(max_length=10, choices=STATUSES, default="pending")
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} import #{self.pk}"
