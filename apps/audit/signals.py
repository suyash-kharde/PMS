from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log(user, "login", "auth", target=user.get_username(), status="success")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    log(user, "logout", "auth", target=user.get_username() if user else "", status="success")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, **kwargs):
    log(None, "login", "auth", target=credentials.get("username", ""), status="failed")
