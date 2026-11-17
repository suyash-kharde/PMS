from apps.projects.selectors import visible_projects

from .models import PenpotWorkspace


def visible_workspaces(user):
    return PenpotWorkspace.objects.filter(
        project__in=visible_projects(user)
    ).select_related("project")


def workspace_for(user, project_pk):
    return visible_workspaces(user).filter(project_id=project_pk).first()
