from django.conf import settings

from apps.accounts.permissions import effective_caps


def ui(request):
    user = getattr(request, "user", None)
    caps = getattr(user, "caps", None) if user is not None else None
    if caps is None and user is not None and user.is_authenticated:
        caps = effective_caps(user)
    unread = 0
    is_admin = False
    if user is not None and user.is_authenticated:
        unread = user.notifications.filter(read_at__isnull=True).count()
        is_admin = user.is_site_admin
    return {
        "APP_NAME": "Planwise",
        "APP_TAGLINE": "Project plan management",
        "user_caps": caps or {},
        "unread_notifications": unread,
        "is_admin": is_admin,
        "HTTPS_PORT": settings.HTTPS_PORT,
        "theme": request.COOKIES.get("theme", "light"),
        "sidebar_collapsed": request.COOKIES.get("sidebar", "") == "collapsed",
    }
