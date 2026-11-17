"""Send email through Microsoft Graph (Outlook) with the app credentials in settings / .env.

Standard library only (urllib), so it needs no extra package. Never raises to the caller:
send_mail() returns (ok, error_text).
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)
_token = {"value": None, "expires": 0.0}


def is_configured():
    return all(getattr(settings, k, "") for k in (
        "MS_GRAPH_TENANT_ID", "MS_GRAPH_CLIENT_ID", "MS_GRAPH_CLIENT_SECRET", "MS_GRAPH_SENDER_ID"))


def _access_token():
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    url = f"https://login.microsoftonline.com/{settings.MS_GRAPH_TENANT_ID}/oauth2/token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": settings.MS_GRAPH_CLIENT_ID,
        "client_secret": settings.MS_GRAPH_CLIENT_SECRET,
        "resource": "https://graph.microsoft.com",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"), timeout=20) as resp:
        data = json.loads(resp.read().decode())
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def send_mail(to_address, subject, html):
    """-> (ok, error). One recipient per call, so nobody sees anyone else's address."""
    if not is_configured():
        return False, "Microsoft Graph mail is not configured (MS_GRAPH_* settings)."
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": False,
    }
    url = f"https://graph.microsoft.com/v1.0/users/{settings.MS_GRAPH_SENDER_ID}/sendMail"
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status in (200, 202), ""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        logger.warning("Graph sendMail to %s failed: %s %s", to_address, exc.code, detail)
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - network / token problems are reported, not raised
        logger.warning("Graph sendMail to %s failed: %s", to_address, exc)
        return False, str(exc)
