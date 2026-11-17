"""Actually writes rows to the database. Only ever called with a batch that was
just re-validated, and only ok rows (no errors) are written — the caller is
responsible for re-validating right before calling this.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.crypto import get_random_string

from apps.accounts.models import User
from apps.projects.models import Customer, Project


def commit_customers(rows):
    created = []
    for r in rows:
        if not r["ok"]:
            continue
        d = r["data"]
        with transaction.atomic():
            customer = Customer.objects.create(
                name=d["name"], code=d["code"], industry=d["industry"], website=d["website"],
                account_manager=d["account_manager"], primary_contact=d["primary_contact"],
                contact_email=d["contact_email"], contact_phone=d["contact_phone"],
                is_active=d["is_active"], description=d["description"], notes=d["notes"],
            )
        created.append({"row": r["row"], "label": customer.name, "url": customer.get_absolute_url()})
    return created


def commit_projects(rows):
    created = []
    for r in rows:
        if not r["ok"]:
            continue
        d = r["data"]
        with transaction.atomic():
            project = Project.objects.create(
                name=d["name"], key=d["key"], customer=d["customer"], description=d["description"],
                status=d["status"], priority=d["priority"], owner=d["owner"], team=d["team"],
                start_date=d["start_date"], due_date=d["due_date"], budget=d["budget"],
            )
        created.append({"row": r["row"], "label": f"{project.key} · {project.name}", "url": project.get_absolute_url()})
    return created


def commit_people(rows):
    created = []
    for r in rows:
        if not r["ok"]:
            continue
        d = r["data"]
        password = d["password"]
        generated = None
        if not password:
            password = get_random_string(14)
            generated = password
        else:
            try:
                validate_password(password)
            except DjangoValidationError:
                generated = get_random_string(14)
                password = generated  # the supplied password was too weak — fall back to a generated one

        with transaction.atomic():
            user = User.objects.create_user(
                username=d["username"], password=password, first_name=d["first_name"],
                last_name=d["last_name"], email=d["email"], phone=d["phone"], role=d["role"],
                team=d["team"], manager=d["manager"], customer=d["customer"], title=d["title"],
                description=d["description"], responsibility=d["responsibility"], remark=d["remark"],
                is_active=d["is_active"],
            )
        created.append({
            "row": r["row"], "label": user.display_name, "url": reverse("accounts:user_detail", args=[user.pk]),
            "generated_password": generated,
        })
    return created


COMMITTERS = {"customers": commit_customers, "projects": commit_projects, "people": commit_people}


def commit(kind, rows):
    return COMMITTERS[kind](rows)
