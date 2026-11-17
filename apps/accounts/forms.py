from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Capability, Role, RoleCapability, Team, User, UserCapability


class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput, required=False, help_text="Leave blank to keep the current password"
    )

    class Meta:
        model = User
        fields = [
            "username", "first_name", "last_name", "email", "role", "team", "manager", "customer", "mfa_required",
            "title", "phone", "color", "description", "responsibility", "remark", "is_active", "is_staff",
        ]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "responsibility": forms.Textarea(attrs={"rows": 2}),
            "remark": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["manager"].queryset = User.objects.exclude(pk=self.instance.pk)
        self.fields["customer"].queryset = self.fields["customer"].queryset.filter(is_active=True)
        # blank = a staff account; data-keep-blank lets the searchable box offer (and keep) "no customer"
        self.fields["customer"].empty_label = "— No customer (staff account) —"
        self.fields["customer"].widget.attrs.update(
            {"data-search": "1", "data-keep-blank": "1", "data-placeholder": "No customer (staff) — search to link one…"}
        )
        # Role and Django-admin access are the two fields on this form that amount to a
        # privilege grant rather than a profile edit — restricted to whoever already holds
        # role.manage (today's admins), even when editing your own account. Anyone else holding
        # plain user.edit (e.g. everyone, on themselves) never sees or can submit these fields —
        # removing them from the form means Django ignores any posted value for them outright,
        # not just hides them in the template.
        from .permissions import has_cap

        if not has_cap(editor, "role.manage"):
            del self.fields["role"]
            del self.fields["is_staff"]
        if not has_cap(editor, "security.manage"):  # who needs MFA is a security decision, like the role
            del self.fields["mfa_required"]

    def clean_manager(self):
        manager = self.cleaned_data.get("manager")
        node, seen = manager, set()
        while node:
            if node.pk == self.instance.pk:
                raise forms.ValidationError("That would create a cycle in the reporting line.")
            if node.pk in seen:
                break
            seen.add(node.pk)
            node = node.manager
        return manager

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd = self.cleaned_data.get("password")
        if pwd:
            user.set_password(pwd)
        if commit:
            user.save()
        return user


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["name", "parent", "rank", "color", "description"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["parent"].queryset = Role.objects.exclude(pk__in=self.instance.descendant_ids())

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        node, seen = parent, set()
        while node:
            if node.pk == self.instance.pk:
                raise forms.ValidationError("Roles cannot inherit from themselves.")
            if node.pk in seen:
                break
            seen.add(node.pk)
            node = node.parent
        return parent


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "parent", "lead", "color", "description"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class CapabilityForm(forms.ModelForm):
    class Meta:
        model = Capability
        fields = ["code", "label", "category", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
