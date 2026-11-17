from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    # management UI — one "Claude Code" tab per project
    path("projects/<int:pk>/mcp/", views.manage, name="manage"),
    path("projects/<int:pk>/mcp/new/", views.token_create, name="token_create"),
    path("projects/<int:pk>/mcp/tokens/<int:token_pk>/revoke/", views.token_revoke, name="token_revoke"),
    # the MCP endpoint itself — the token IS the credential, so it sits last and greedy.
    # `/mcp/` (no path token) is the log-safe form: pass the token as `Authorization: Bearer`.
    path("mcp/", views.endpoint, {"token": None}, name="endpoint_header"),
    path("mcp/<str:token>/", views.endpoint, name="endpoint"),
]
