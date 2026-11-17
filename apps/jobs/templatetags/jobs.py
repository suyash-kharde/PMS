from django import template

from .. import services
from ..models import ACTIVE, Job

register = template.Library()


@register.inclusion_tag("jobs/_banners.html")
def job_banners(project=None, kinds="", target=None):
    """Queue position / estimate banners for the waiting or running jobs a page cares about,
    e.g. {% job_banners project=project kinds="brd_update,brd_regenerate" %}."""
    jobs = Job.objects.filter(status__in=ACTIVE).order_by("created_at")
    if project is not None:
        jobs = jobs.filter(project=project)
    if kinds:
        jobs = jobs.filter(kind__in=[k.strip() for k in kinds.split(",") if k.strip()])
    if target is not None:
        jobs = jobs.filter(target_id=getattr(target, "pk", target))
    return {"banners": [services.describe(j) for j in jobs[:5]]}
