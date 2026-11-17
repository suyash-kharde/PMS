import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services, vapid
from .models import Notification, PushSubscription


@never_cache
def service_worker(request):
    """Served from the site root so its scope covers the whole origin."""
    body = render_to_string("notify/sw.js")
    response = HttpResponse(body, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


@login_required
def public_key(request):
    key = vapid.public_key()
    return JsonResponse({"publicKey": key, "enabled": bool(key), "secure": request.is_secure()})


@login_required
@require_POST
def subscribe(request):
    try:
        payload = json.loads(request.body or "{}")
        endpoint = payload["endpoint"]
        keys = payload["keys"]
    except (ValueError, KeyError):
        return JsonResponse({"ok": False, "error": "malformed subscription"}, status=400)

    subscription, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": keys.get("p256dh", ""),
            "auth": keys.get("auth", ""),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
            "failures": 0,
        },
    )
    return JsonResponse({"ok": True, "created": created, "id": subscription.pk})


@login_required
@require_POST
def unsubscribe(request):
    payload = json.loads(request.body or "{}")
    endpoint = payload.get("endpoint", "")
    deleted = PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()[0]
    return JsonResponse({"ok": True, "deleted": deleted})


@login_required
@require_POST
def send_test(request):
    notification = services.notify(
        request.user,
        title="Planwise test notification",
        body="Push is working. You will get these when a recording finishes or the BRD changes.",
        url="/notifications/",
        kind="test",
    )
    return JsonResponse(
        {
            "ok": True,
            "pushed": notification.pushed,
            "error": notification.push_error,
            "subscriptions": PushSubscription.objects.filter(user=request.user).count(),
        }
    )


@login_required
def inbox(request):
    notifications = request.user.notifications.select_related("project")[:100]
    return render(
        request,
        "notify/inbox.html",
        {
            "notifications": notifications,
            "unread": request.user.notifications.filter(read_at__isnull=True).count(),
            "subscriptions": PushSubscription.objects.filter(user=request.user),
            "vapid_ready": bool(vapid.public_key()),
        },
    )


@login_required
def unread_feed(request):
    """Polled by the bell so the badge updates without a reload."""
    rows = request.user.notifications.filter(read_at__isnull=True)[:10]
    return JsonResponse(
        {
            "unread": request.user.notifications.filter(read_at__isnull=True).count(),
            "items": [
                {"id": n.pk, "title": n.title, "body": n.body, "url": n.url, "kind": n.kind,
                 "at": n.created_at.isoformat()}
                for n in rows
            ],
        }
    )


@login_required
@require_POST
def mark_read(request, pk=None):
    """Marks one (or all) of the user's unread notifications read. A plain POST just stays on
    the inbox (the ✓ button); posting `open=1` for a single notification (clicking its title —
    see inbox.html) also sends the user on to wherever it points, so opening a notification and
    marking it read happen in the same click instead of the title being a dead link that never
    touched read_at at all."""
    rows = request.user.notifications.filter(read_at__isnull=True)
    if pk:
        rows = rows.filter(pk=pk)
    count = rows.update(read_at=timezone.now())
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True, "marked": count})
    if pk and request.POST.get("open"):
        target = request.user.notifications.filter(pk=pk).values_list("url", flat=True).first()
        if target:
            return redirect(target)
    return redirect("notify:inbox")
