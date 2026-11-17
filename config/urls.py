from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import include, path, re_path, reverse
from django.utils.http import urlencode

from apps.core.views import media_serve
from apps.notify.views import service_worker


def _admin_login(request, extra_context=None):
    """The Django admin has its own sign-in page that would skip the emailed login code; send it to ours."""
    if request.user.is_authenticated:
        raise PermissionDenied("This account can't use the Django admin.")
    return redirect(reverse("accounts:login") + "?" + urlencode({"next": request.GET.get("next", "/django-admin/")}))


admin.site.login = _admin_login

urlpatterns = [
    # must sit at the site root: a service worker can only control its own scope
    path("sw.js", service_worker, name="service_worker"),
    path("django-admin/", admin.site.urls),
    path("", include("apps.notify.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.projects.urls")),
    path("", include("apps.tickets.urls")),
    path("", include("apps.sprints.urls")),
    path("", include("apps.intake.urls")),
    path("", include("apps.aimodels.urls")),
    path("", include("apps.bulk.urls")),
    path("", include("apps.rag.urls")),
    path("", include("apps.mcp.urls")),
    path("", include("apps.devlog.urls")),
    path("", include("apps.penpot.urls")),
    path("", include("apps.vms.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.timesheet.urls")),
    path("", include("apps.jobs.urls")),
    # uploads: login-gated and Range-capable, so recordings can be seeked but not shared
    re_path(r"^media/(?P<path>.*)$", media_serve, name="media"),
]
