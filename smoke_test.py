"""Smoke test: every page for several user types, plus the JSON endpoints.

    docker compose exec web python smoke_test.py

Builds its own scratch customer/project/users (see smoke_fixtures.py) and removes them
afterwards, so it neither depends on nor disturbs real data.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings

# In-process smoke run: the Django test client uses HTTP_HOST "testserver", which a
# production .env (ALLOWED_HOSTS restricted to real hostnames) rejects. This is a test
# script building its own fixtures, so widening the host list here is safe.
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver", "localhost", "127.0.0.1"]

import json

from django.test import Client

import smoke_fixtures
from apps.accounts.models import Role, User
from apps.accounts.permissions import effective_caps
from apps.projects.models import Task
from apps.projects.selectors import visible_customers, visible_projects

fails = []


def make_self_signed_pem():
    """A real certificate, so the TLS layer actually loads it."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "smoke-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def check(label, response, expected=(200,)):
    code = getattr(response, "status_code", response)
    ok = code in expected
    print(f"  {'ok  ' if ok else 'FAIL'} {code:<4} {label}")
    if not ok:
        fails.append(f"{label} -> {code}")


data = smoke_fixtures.build()
project = data["project"]
customer = data["customer"]
task = project.tasks.first()
role = Role.objects.get(slug="lead")
admin = User.objects.filter(is_superuser=True).first()

try:
    accounts = [("admin", admin)] + [(name, user) for name, user in data["users"].items()]

    for name, user in accounts:
        if user is None:
            continue
        client = Client()
        client.force_login(user)
        print(f"\n[{name}] role={user.role.name if user.role_id else '-'}")
        check("dashboard", client.get("/dashboard/"))
        check("customers", client.get("/customers/"), (200, 403))
        check("customer detail", client.get(f"/customers/{customer.pk}/"), (200, 403, 404))
        check("projects", client.get("/projects/"))
        check("projects?view=table", client.get("/projects/?view=table&status=active"))
        check("my tasks", client.get("/my-tasks/"))
        check("search", client.get("/search/?q=smoke"))
        for tab in ["board", "list", "plan", "gantt", "activity"]:
            check(f"project {tab}", client.get(f"/projects/{project.pk}/?tab={tab}"), (200, 404))
        # the People tab lives on the participants page; the old query tab redirects there
        check("project people redirects", client.get(f"/projects/{project.pk}/?tab=people"), (302, 404))
        check("task detail", client.get(f"/tasks/{task.pk}/"), (200, 404))
        check("timeline", client.get(f"/projects/{project.pk}/timeline/"), (200, 403, 404))
        check("participants", client.get(f"/projects/{project.pk}/participants/"), (200, 403, 404))
        check("brd", client.get(f"/projects/{project.pk}/brd/"), (200, 403, 404))
        check("people", client.get("/users/"), (200, 403))
        check("org chart", client.get("/org-chart/"), (200, 403))
        check("own profile", client.get(f"/users/{user.pk}/"))
        check("roles", client.get("/roles/"), (200, 403))
        check("role detail", client.get(f"/roles/{role.pk}/"), (200, 403))
        check("capabilities", client.get("/capabilities/"), (200, 403))
        check("teams", client.get("/teams/"), (200, 403))
        check("model services", client.get("/service-status/"), (200, 403))
        check("new project form", client.get("/projects/new/"), (200, 403))
        check("new customer form", client.get("/customers/new/"), (200, 403))
        check("new task form", client.get(f"/projects/{project.pk}/tasks/new/"), (200, 403, 404))

    print("\n[people management]")
    boss = Client()
    boss.force_login(data["users"]["director"])
    before = project.memberships.count()
    resp = boss.post(f"/projects/{project.pk}/participants/new-person/", {
        "first_name": "Smokeperson", "last_name": "Temp", "username": "", "email": "newby@example.com",
        "phone": "", "password": "", "side": "client", "position": "Client SME",
        "role_description": "Subject matter expert.", "responsibility": "Signs off the process design.",
        "remarks": "Part time.", "allocation": 100,
    })
    check("create a new person", resp, (302,))
    added = project.memberships.filter(user__first_name="Smokeperson").first()
    check("person joined the project", 200 if added else 500)
    check("membership carries the brief",
          200 if added and added.responsibility and added.side == "client" else 500)
    check("membership count grew", 200 if project.memberships.count() == before + 1 else 500)
    if added:
        check("edit an existing brief", boss.post(
            f"/projects/{project.pk}/participants/{added.pk}/save/",
            {
                f"m{added.pk}-user": str(added.user_id), f"m{added.pk}-side": "client",
                f"m{added.pk}-position": "Client SME", f"m{added.pk}-role_description": "Updated brief.",
                f"m{added.pk}-responsibility": "Still signs off.", f"m{added.pk}-remarks": "",
                f"m{added.pk}-allocation": "50",
            },
        ), (302,))
        added.refresh_from_db()
        check("brief was updated", 200 if added.role_description == "Updated brief." else 500)
        check("duplicate member rejected", boss.post(
            f"/projects/{project.pk}/participants/save/",
            {"user": str(added.user_id), "side": "client", "allocation": "100"},
        ), (200,))  # re-renders the form with the error rather than redirecting
        check("remove a participant", boss.post(
            f"/projects/{project.pk}/participants/{added.pk}/delete/"), (302,))
    member_client = Client()
    member_client.force_login(data["users"]["member"])
    check("member cannot add people", member_client.post(
        f"/projects/{project.pk}/participants/new-person/", {"first_name": "Nope", "side": "client"}), (403,))

    print("\n[AI model endpoints]")
    from django.core.files.base import ContentFile

    from apps.aimodels import runtime
    from apps.aimodels.models import ModelProvider

    admin_client = Client()
    admin_client.force_login(admin)
    check("AI models page", admin_client.get("/ai-models/"), (200,))
    check("new endpoint form", admin_client.get("/ai-models/new/"), (200,))
    plain = Client()
    plain.force_login(data["users"]["member"])
    check("member cannot open it", plain.get("/ai-models/"), (403,))

    provider = ModelProvider.objects.create(
        kind="other", name="Smoke endpoint", base_url="https://example.invalid/v1",
        model_name="smoke/model-1", api_key="secret-key-value", timeout=5,
    )
    try:
        check("endpoint detail", admin_client.get(f"/ai-models/{provider.pk}/"), (200,))
        body = admin_client.get(f"/ai-models/{provider.pk}/").content.decode()
        check("API key is never rendered", 200 if "secret-key-value" not in body else 500)
        check("key is masked instead", 200 if provider.masked_key in body else 500)

        provider.ca_cert.save("smoke-ca.crt", ContentFile(make_self_signed_pem()))
        provider.refresh_from_db()
        check("uploaded CA becomes the trust store",
              200 if str(provider.httpx_kwargs().get("verify", "")).endswith("smoke-ca.crt") else 500)

        check("test button reaches the endpoint", admin_client.post(
            f"/api/ai-models/{provider.pk}/test/", "{}", content_type="application/json"), (200,))
        provider.refresh_from_db()
        check("failure is recorded on the row", 200 if provider.last_status == "error" else 500)

        check("blank key keeps the stored one", admin_client.post(f"/ai-models/{provider.pk}/edit/", {
            "kind": "other", "name": "Smoke endpoint", "provider_type": "openai",
            "base_url": "https://example.invalid/v1", "model_name": "smoke/model-1", "api_key": "",
            "verify_ssl": "on", "timeout": 5, "max_tokens": 100, "context_tokens": 16000,
            "temperature": 0.2,
            "language": "", "extra": "{}", "is_active": "on",
        }), (302,))
        provider.refresh_from_db()
        check("key survived the edit", 200 if provider.api_key == "secret-key-value" else 500)

        check("make default", admin_client.post(f"/ai-models/{provider.pk}/default/"), (302,))
        provider.refresh_from_db()
        check("resolution now points at it", 200 if runtime.config_for(
            "other", use_cache=False)["base_url"] == "https://example.invalid/v1" else 500)
        check("drop the certificate", admin_client.post(
            f"/ai-models/{provider.pk}/cert/ca_cert/delete/"), (302,))

        llm_config = runtime.config_for("llm", use_cache=False)
        print(f"       LLM -> {llm_config['model']} @ {llm_config['base_url']} ({llm_config['source']})")
        check("LLM resolves to a configured endpoint", 200 if llm_config["base_url"] else 500)
        asr_config = runtime.config_for("asr", use_cache=False)
        print(f"       ASR -> {asr_config['model']} @ {asr_config['base_url']} ({asr_config['source']})")
        check("ASR resolves to a configured endpoint", 200 if asr_config["model"] else 500)
        check("seeded endpoints exist",
              200 if ModelProvider.objects.filter(is_default=True).count() >= 4 else 500)
    finally:
        if provider.ca_cert:
            provider.ca_cert.delete(save=False)
        provider.delete()
        runtime.invalidate()

    print("\n[JSON endpoints as the manager]")
    client = Client()
    client.force_login(data["users"]["manager"])
    states = list(project.states)
    check("task move", client.post(
        "/api/tasks/move/",
        json.dumps({"task": task.pk, "state": states[-1].pk, "order": [task.pk]}),
        content_type="application/json",
    ))
    resp = client.post(
        "/api/tasks/quick-create/",
        json.dumps({"project": project.pk, "title": "Smoke quick create", "state": states[0].pk}),
        content_type="application/json",
    )
    check("quick create", resp)
    new_id = resp.json().get("task", {}).get("id")
    check("task patch", client.post(
        f"/api/tasks/{new_id}/patch/", json.dumps({"priority": 1}), content_type="application/json"
    ))
    check("project stats", client.get(f"/api/projects/{project.pk}/stats/"))

    print("\n[permission boundaries]")
    guest = Client()
    guest.force_login(data["users"]["guest"])
    check("guest cannot open roles admin", guest.get("/roles/"), (403,))
    check("guest cannot create project", guest.get("/projects/new/"), (403,))
    member = Client()
    member.force_login(data["users"]["member"])
    check("member cannot manage roles", member.get("/capabilities/"), (403,))
    check("member cannot create a customer", member.get("/customers/new/"), (403,))
    check("member cannot regenerate the BRD", member.post(f"/projects/{project.pk}/brd/regenerate/"), (403,))
    check("anonymous is redirected", Client().get("/dashboard/"), (302,))

    print("\n[logging]")
    import logging as _logging

    _lg = _logging.getLogger("django.request")
    # DEBUG=0 with no LOGGING config routes request exceptions only to mail_admins,
    # so with no ADMINS a 500 leaves no traceback anywhere. The config in settings.py
    # must send them to a stream handler. Guards a silent-500 regression (REMEDIATION 6.5).
    _has_stream = any(isinstance(h, _logging.StreamHandler) for h in _lg.handlers) or (
        _lg.propagate and any(isinstance(h, _logging.StreamHandler) for h in _logging.getLogger().handlers)
    )
    _ok = _has_stream and _lg.getEffectiveLevel() <= _logging.ERROR
    check("django.request 500s reach a stream handler", 200 if _ok else 500)

    print("\n[scope resolution]")
    for name in ["director", "manager", "lead", "member", "guest"]:
        user = data["users"][name]
        caps = effective_caps(user, use_cache=False)
        print(f"  {name:<9} caps={len(caps):<3} project.view={caps.get('project.view')} "
              f"customer.view={caps.get('customer.view')} projects={visible_projects(user).count()} "
              f"customers={visible_customers(user).count()} subordinates={len(user.subordinate_ids()) - 1}")

finally:
    smoke_fixtures.teardown()
    Task.objects.filter(title="Smoke quick create").delete()
    # the new-person endpoint creates a real account; removing the membership is not enough
    User.objects.filter(first_name="Smokeperson", is_superuser=False).delete()

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILURES:\n" + "\n".join(fails)))
raise SystemExit(1 if fails else 0)
