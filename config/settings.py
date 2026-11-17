import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [
    o
    for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,"
        f"https://localhost:{os.environ.get('HTTPS_PORT', '8443')},"
        f"https://127.0.0.1:{os.environ.get('HTTPS_PORT', '8443')}",
    ).split(",")
    if o
]

INSTALLED_APPS = [
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.projects",
    "apps.tickets",
    "apps.sprints",
    "apps.intake",
    "apps.notify",
    "apps.aimodels",
    "apps.core",
    "apps.bulk",
    "apps.rag",
    "apps.mcp",
    "apps.devlog",
    "apps.penpot",
    "apps.vms",
    "apps.audit",
    "apps.timesheet",
    "apps.jobs",
]

# behind the nginx TLS terminator
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
HTTPS_PORT = os.environ.get("HTTPS_PORT", "8443")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@example.com")

# uploads (audio / documents) — bind-mounted with the source, so they survive rebuilds
DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
# raised for the VM "upload code" feature — a whole project folder can genuinely be thousands
# of small files; Django's defaults (100 files / 1000 fields) reject that well before our own
# app-level size cap ever gets a say.
DATA_UPLOAD_MAX_NUMBER_FILES = 5000
DATA_UPLOAD_MAX_NUMBER_FIELDS = 12000

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.OtpRequiredMiddleware",
    "apps.accounts.middleware.CapabilityMiddleware",
    "apps.accounts.middleware.PasswordChangeRequiredMiddleware",
    "apps.accounts.middleware.CustomerLinksOnlyMiddleware",
    "apps.timesheet.middleware.CurrentUserMiddleware",
    "apps.audit.middleware.CurrentUserMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.ui",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "pms"),
        "USER": os.environ.get("POSTGRES_USER", "pms"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "pms_password"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Under a threaded ASGI server (gunicorn + UvicornWorker) every worker thread
        # opens its own connection and Django's close_old_connections often runs on a
        # different thread than the one holding it, so persistent connections
        # (CONN_MAX_AGE > 0) accumulate until Postgres runs out of slots. Keep this at
        # 0 for the ASGI deployment; raise it only behind a real pooler (pgbouncer or
        # psycopg's pool).
        "CONN_MAX_AGE": int(os.environ.get("CONN_MAX_AGE", "0")),
    }
}

# cache stored in postgres (table created by entrypoint `createcachetable`)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "pms_cache",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 10000, "CULL_FREQUENCY": 3},
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LOGIN_URL = "/login/"
# Planwise's own cookie names. Project sites served at /sites/<name>/ share this domain, and nginx
# strips exactly these two before forwarding there, so a project app (often Django too, using the
# default "sessionid"/"csrftoken") keeps its own cookies and never sees Planwise's session.
SESSION_COOKIE_NAME = "planwise_sessionid"
CSRF_COOKIE_NAME = "planwise_csrftoken"
# Timesheet attendance window (local time): logging in or using Planwise inside it = Present;
# nothing inside it on a working day = Absent for that day.
TIMESHEET_WORK_START = os.environ.get("TIMESHEET_WORK_START", "00:00")
TIMESHEET_WORK_END = os.environ.get("TIMESHEET_WORK_END", "18:30")

# Outbound email through Microsoft Graph (client-credentials app). Secrets live in .env only.
MS_GRAPH_TENANT_ID = os.environ.get("MS_GRAPH_TENANT_ID", "")
MS_GRAPH_CLIENT_ID = os.environ.get("MS_GRAPH_CLIENT_ID", "")
MS_GRAPH_CLIENT_SECRET = os.environ.get("MS_GRAPH_CLIENT_SECRET", "")
MS_GRAPH_SENDER_ID = os.environ.get("MS_GRAPH_SENDER_ID", "")  # mailbox (user object id) the mail is sent from
LOGIN_REMINDER_ENABLED = os.environ.get("LOGIN_REMINDER_ENABLED", "0") == "1"
LOGIN_REMINDER_TIME = os.environ.get("LOGIN_REMINDER_TIME", "10:15")
# Fridays: mail everyone who has an unsubmitted timesheet week this month. Off unless TIMESHEET_REMINDER_ENABLED=1.
TIMESHEET_REMINDER_ENABLED = os.environ.get("TIMESHEET_REMINDER_ENABLED", "0") == "1"
TIMESHEET_REMINDER_TIME = os.environ.get("TIMESHEET_REMINDER_TIME", "12:00")
# One-time login codes by email on every sign-in (apps/accounts/otp.py). Off unless LOGIN_OTP_ENABLED=1.
# LOGIN_OTP_USERS="renuka,suyash" limits it to those usernames (a pilot); empty = everyone.
# Authenticator-app MFA (apps/accounts/mfa.py). Who needs it is set on the Security page and on each person.
# MFA_FORCE_OFF=1 switches it off for everyone (an emergency brake that needs no login); recreate web to apply.
MFA_FORCE_OFF = os.environ.get("MFA_FORCE_OFF", "0") == "1"
LOGIN_OTP_ENABLED = os.environ.get("LOGIN_OTP_ENABLED", "0") == "1"
LOGIN_OTP_USERS = {u.strip().lower() for u in os.environ.get("LOGIN_OTP_USERS", "").split(",") if u.strip()}
LOGIN_OTP_TTL_SECONDS = int(os.environ.get("LOGIN_OTP_TTL_SECONDS", "600"))
LOGIN_OTP_MAX_ATTEMPTS = int(os.environ.get("LOGIN_OTP_MAX_ATTEMPTS", "5"))
LOGIN_OTP_RESEND_SECONDS = int(os.environ.get("LOGIN_OTP_RESEND_SECONDS", "60"))
LOGIN_OTP_MAX_RESENDS = int(os.environ.get("LOGIN_OTP_MAX_RESENDS", "5"))
# Self-service sign-up (/signup/) is closed: accounts are created by an administrator. ALLOW_SIGNUP=1 reopens it.
ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "0") == "1"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# a user with no activity for this long is logged out on their next request
SESSION_IDLE_TIMEOUT_SECONDS = 2 * 60 * 60

# generic Create/Edit/Delete tracking for every model not already hand-instrumented in
# apps/audit/generic.py — a safety switch to disable it instantly if it ever misbehaves
AUDIT_AUTO_TRACK = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Content-hashed filenames (main.abc123.css) so nginx's 7-day static cache is always safe:
    # a changed file gets a new URL instead of being stuck behind the old cached one.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# With DEBUG=0 and no ADMINS, Django's default config swallows request exceptions
# silently — a 500 shows in the access log with no traceback. Send them to the
# container's stdout so `docker logs` shows the actual error.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.db.backends": {"level": "WARNING"},
    },
}

# Usernames that can be picked as the assignee on any ticket, whatever its project or team —
# always listed on the ticket form and never hidden by its Team filter.
TICKET_ALWAYS_ASSIGNEES = ["YashPatil", "Suyash", "renuka"]

# ---------------------------------------------------------------- Penpot (design)
# Per-project isolated design workspaces on a self-hosted Penpot instance. When
# PENPOT_ENABLED is on, creating a Planwise project queues a Penpot bot account +
# its own team + a starter file; the project's Design tab links straight to it.
PENPOT_ENABLED = os.environ.get("PENPOT_ENABLED", "0") == "1"
PENPOT_BASE_URL = os.environ.get("PENPOT_BASE_URL", "http://172.16.16.214:9001").rstrip("/")
# Domain used to mint per-project bot addresses, e.g. penpot+plat@<domain>.
PENPOT_BOT_EMAIL_DOMAIN = os.environ.get("PENPOT_BOT_EMAIL_DOMAIN", "planwise.local")
PENPOT_VERIFY_SSL = os.environ.get("PENPOT_VERIFY_SSL", "1") == "1"
PENPOT_TIMEOUT = float(os.environ.get("PENPOT_TIMEOUT", "30"))
# People to invite to every project's Penpot team (comma-separated emails) — e.g.
# a shared design account. Per-project members are invited from the Design tab.
PENPOT_DEFAULT_INVITES = [
    e.strip() for e in os.environ.get("PENPOT_DEFAULT_INVITES", "").split(",") if e.strip()
]
# Public URL of the shared Penpot MCP service (the deployed `penpotapp/mcp` running in
# --multi-user / remote mode). This is what Claude Code on a developer's laptop connects
# to as the `penpot` MCP server. Blank = not exposed yet; the Design tab then tells the
# developer to ask an admin to publish it rather than showing a broken command.
PENPOT_MCP_URL = os.environ.get("PENPOT_MCP_URL", "").rstrip("/")
