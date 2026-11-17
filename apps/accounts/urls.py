from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.OtpLoginView.as_view(), name="login"),
    path("login/otp/", views.login_otp, name="login_otp"),
    path("login/mfa/", views.login_mfa, name="login_mfa"),
    path("mfa/backup-codes/", views.mfa_backup_codes, name="mfa_backup_codes"),
    path("users/<int:pk>/mfa-reset/", views.mfa_reset, name="mfa_reset"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("password/", views.password_change, name="password_change"),
    path("settings/security/", views.security_settings, name="security_settings"),

    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_form, name="user_create"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/tickets/<str:kind>/", views.user_tickets, name="user_tickets"),
    path("users/<int:pk>/edit/", views.user_form, name="user_edit"),
    path("users/<int:pk>/toggle/", views.user_toggle_active, name="user_toggle"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),
    path("users/<int:pk>/override/", views.user_cap_override, name="user_cap_override"),
    path("users/<int:pk>/permissions/", views.user_permissions, name="user_permissions"),
    path("org-chart/", views.org_chart, name="org_chart"),

    path("roles/", views.role_list, name="role_list"),
    path("roles/new/", views.role_save, name="role_create"),
    path("roles/<int:pk>/", views.role_detail, name="role_detail"),
    path("roles/<int:pk>/save/", views.role_save, name="role_save"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
    path("roles/<int:pk>/grant/", views.role_grant, name="role_grant"),
    path("capabilities/", views.capability_list, name="capability_list"),

    path("teams/", views.team_list, name="team_list"),
    path("teams/new/", views.team_save, name="team_create"),
    path("teams/<int:pk>/save/", views.team_save, name="team_save"),
    path("teams/<int:pk>/delete/", views.team_delete, name="team_delete"),
    path("teams/<int:pk>/add-members/", views.team_add_members, name="team_add_members"),
]
