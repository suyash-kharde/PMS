"""set_bulk_role: dry run by default, protects named people, administrators and client logins."""
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt


class SetBulkRoleTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        self.bda = Role.objects.create(name="BDA", slug="bda")
        self.member = Role.objects.create(name="Member", slug="member")
        self.customer = Role.objects.create(name="Customer", slug="customer")
        self.boss = Role.objects.create(name="Administrator", slug="administrator")
        cap, _ = Capability.objects.get_or_create(code="role.manage", defaults={"label": "x"})
        RoleCapability.objects.create(role=self.boss, capability=cap, scope="all")

        def make(username, first, last, role):
            return User.objects.create_user(username, password="x", first_name=first, last_name=last, role=role)

        self.yash = make("yash.p", "Yash", "Patil", self.member)
        self.suyash = make("suyash.k", "Suyash", "Kharde", self.member)
        self.abhishek = make("abhishek.h", "Abhishek", "Hengade", self.member)
        self.sadhna = make("sadhna", "Sadhna", "Rao", self.member)
        self.renuka = make("renuka", "Renuka", "Opalkar", self.member)
        self.admin = make("siteadmin", "Site", "Admin", self.boss)
        self.client_user = make("acme.user", "Acme", "Contact", self.customer)

    def run_cmd(self, *args):
        out = StringIO()
        call_command("set_bulk_role", "--role", "bda", *args, stdout=out)
        return out.getvalue()

    def role_of(self, user):
        user.refresh_from_db()
        return user.role.slug

    def test_dry_run_changes_nothing(self):
        text = self.run_cmd("--except", "Yash")
        self.assertIn("Dry run only", text)
        self.assertEqual(self.role_of(self.sadhna), "member")

    def test_apply_changes_everyone_except_the_named_and_protected(self):
        self.run_cmd("--except", "Yash", "Suyash", "Abhishek Hengade", "--apply")
        self.assertEqual(self.role_of(self.sadhna), "bda")
        self.assertEqual(self.role_of(self.renuka), "bda")
        for kept in (self.yash, self.suyash, self.abhishek):
            self.assertEqual(self.role_of(kept), "member")
        self.assertEqual(self.role_of(self.admin), "administrator")
        self.assertEqual(self.role_of(self.client_user), "customer")

    def test_yash_does_not_match_suyash(self):
        self.run_cmd("--except", "Yash", "--apply")
        self.assertEqual(self.role_of(self.yash), "member")
        self.assertEqual(self.role_of(self.suyash), "bda")

    def test_unmatched_name_blocks_apply(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--except", "Nobody Here", "--apply")
        self.assertEqual(self.role_of(self.sadhna), "member")

    def test_include_admins_flag_changes_administrators(self):
        self.run_cmd("--include-admins", "--apply")
        self.assertEqual(self.role_of(self.admin), "bda")
