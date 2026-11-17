"""VM registry page."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from .models import VM, ProjectVM


class VmSearchTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.root = User.objects.create_superuser("root", password="x")
        self.a = VM.objects.create(name="pms-dev", host="10.0.0.5", os="Ubuntu 22.04", username="deploy", password="s3cret")
        self.b = VM.objects.create(name="crm-prod", host="crm.example.com", os="Windows Server")
        self.c = VM.objects.create(name="spare", host="10.0.9.9")
        project = Project.objects.create(key="PMS", name="Planwise")
        ProjectVM.objects.create(project=project, vm=self.a)
        ProjectVM.objects.create(project=project, vm=self.b)
        self.client.force_login(self.root)

    def _names(self, **params):
        page = self.client.get(reverse("vms:vm_list"), params)
        self.assertEqual(page.status_code, 200)
        return page, sorted(vm.name for vm, _form in page.context["rows"])

    def test_vs01_no_search_lists_everything(self):
        page, names = self._names()
        self.assertEqual(names, ["crm-prod", "pms-dev", "spare"])
        self.assertContains(page, "3 VMs")

    def test_vs02_search_by_name_host_os_and_username(self):
        self.assertEqual(self._names(q="CRM")[1], ["crm-prod"])             # name, any case
        self.assertEqual(self._names(q="10.0.9")[1], ["spare"])             # IP
        self.assertEqual(self._names(q="windows")[1], ["crm-prod"])         # OS
        self.assertEqual(self._names(q="deploy")[1], ["pms-dev"])           # login name

    def test_vs03_search_by_project_and_count_stays_right(self):
        page, names = self._names(q="planwise")                              # a project on the VM
        self.assertEqual(names, ["crm-prod", "pms-dev"])
        self.assertEqual({vm.name: vm.n for vm, _f in page.context["rows"]}, {"crm-prod": 1, "pms-dev": 1})
        self.assertContains(page, "2 of 3 VMs match")

    def test_vs04_passwords_are_not_searchable(self):
        page, names = self._names(q="s3cret")
        self.assertEqual(names, [])
        self.assertContains(page, "No VM matches")      # the word is only echoed back as the search text

    def test_vs05_search_needs_the_same_permission(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("vms:vm_list"), {"q": "x"}).status_code, 302)  # sent to sign in

    def test_vs06_filters_by_os_project_and_usage(self):
        self.assertEqual(self._names(os="Windows Server")[1], ["crm-prod"])
        self.assertEqual(self._names(project=str(Project.objects.get().pk))[1], ["crm-prod", "pms-dev"])
        self.assertEqual(self._names(use="used")[1], ["crm-prod", "pms-dev"])
        self.assertEqual(self._names(use="unused")[1], ["spare"])
        self.assertEqual(self._names(q="10.0", use="used")[1], ["pms-dev"])          # search and filters combine
        page, names = self._names(os="Ubuntu 22.04", use="unused")
        self.assertEqual(names, [])
        self.assertContains(page, "No VM matches")
        self.assertEqual(self._names(project="not-a-number")[1], ["crm-prod", "pms-dev", "spare"])  # junk is ignored

    def test_vs07_sorting(self):
        order = lambda sort: [vm.name for vm, _f in self.client.get(reverse("vms:vm_list"), {"sort": sort}).context["rows"]]  # noqa: E731
        self.assertEqual(order("name"), ["crm-prod", "pms-dev", "spare"])
        self.assertEqual(order("name_desc"), ["spare", "pms-dev", "crm-prod"])
        self.assertEqual(order("newest")[0], "spare")                                  # created last
        self.assertEqual(order("projects")[-1], "spare")                               # no projects: last
        self.assertEqual(order("nonsense"), ["crm-prod", "pms-dev", "spare"])          # unknown sort -> by name

    def test_vs08_filter_choices_are_listed(self):
        page = self.client.get(reverse("vms:vm_list"))
        self.assertContains(page, "Ubuntu 22.04")
        self.assertContains(page, "PMS – Planwise")
        self.assertContains(page, "Not used (no projects)")

