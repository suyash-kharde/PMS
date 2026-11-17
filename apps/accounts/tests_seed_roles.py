"""The start-up seeding must not bring back roles or permissions an admin removed."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Role, RoleCapability


def seed(*args):
    call_command("seed_demo", "--config-only", *args, stdout=StringIO())


class StartupSeedingKeepsAdminChoicesTests(TestCase):
    def test_a_fresh_database_gets_the_built_in_roles(self):
        seed()
        self.assertTrue(Role.objects.filter(slug="guest").exists())
        self.assertTrue(Role.objects.filter(slug="member").exists())

    def test_deleted_roles_stay_deleted_after_a_restart(self):
        seed()
        Role.objects.filter(slug__in=["guest", "member"]).delete()
        seed()  # what the container does on every start
        self.assertFalse(Role.objects.filter(slug__in=["guest", "member"]).exists())

    def test_removed_permissions_and_a_changed_parent_stay_as_set(self):
        seed()
        lead = Role.objects.get(slug="lead")
        RoleCapability.objects.filter(role=lead, capability__code="task.delete").delete()
        Role.objects.filter(slug="lead").update(parent=None)
        seed()
        lead.refresh_from_db()
        self.assertFalse(RoleCapability.objects.filter(role=lead, capability__code="task.delete").exists())
        self.assertIsNone(lead.parent)

    def test_reseed_flag_brings_the_built_in_roles_back_on_request(self):
        seed()
        Role.objects.filter(slug="guest").delete()
        seed("--reseed-roles")
        self.assertTrue(Role.objects.filter(slug="guest").exists())

    def test_new_permission_codes_are_still_synced(self):
        from apps.accounts.models import Capability

        seed()
        Capability.objects.filter(code="chat.view").delete()
        seed()
        self.assertTrue(Capability.objects.filter(code="chat.view").exists())
