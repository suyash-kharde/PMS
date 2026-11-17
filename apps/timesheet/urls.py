from django.urls import path

from . import views

app_name = "timesheet"

urlpatterns = [
    path("timesheet/", views.week_view, name="week"),
    path("timesheet/people/", views.people_view, name="people"),
    path("timesheet/people/today/<str:show>/", views.people_today, name="people_today"),
    path("timesheet/calendar/", views.calendar_view, name="calendar"),
    path("timesheet/holidays/", views.holidays, name="holidays"),
    path("timesheet/holidays/<int:pk>/edit/", views.holiday_edit, name="holiday_edit"),
    path("timesheet/holidays/<int:pk>/delete/", views.holiday_delete, name="holiday_delete"),
    path("timesheet/holidays/follow/", views.holiday_link, name="holiday_link"),
    path("timesheet/working-days/add/", views.working_day_add, name="working_day_add"),
    path("timesheet/working-days/<int:pk>/edit/", views.working_day_edit, name="working_day_edit"),
    path("timesheet/working-days/<int:pk>/delete/", views.working_day_delete, name="working_day_delete"),
    path("timesheet/attendance/", views.attendance, name="attendance"),
    path("timesheet/reports/", views.reports, name="reports"),
    path("timesheet/export/attendance/", views.attendance_export_xlsx, name="attendance_export"),
    path("timesheet/people/<int:pk>/settings/", views.person_settings, name="person_settings"),
    path("timesheet/people/<int:pk>/main-project/", views.main_project, name="main_project"),
]
