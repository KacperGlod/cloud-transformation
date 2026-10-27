"""Saved project access; public projects grant scoped, read-only viewing."""
from uuid import UUID

from fastapi import HTTPException

from .models import Cloud, Project, iso_utc
from .services import cloud_json, get_cloud


def get_project(session, identifier: UUID, user, *, write=True) -> Project:
    project = session.get(Project, identifier)
    if project is None:
        raise HTTPException(404, "Nie znaleziono projektu.")
    owns = user is not None and project.owner_id == user.id
    if not owns and (write or project.visibility != "public"):
        if user is None:
            raise HTTPException(401, "Zaloguj się, aby otworzyć prywatny projekt.")
        raise HTTPException(404, "Nie znaleziono projektu.")
    return project


def project_cloud(session, identifier, user) -> Cloud:
    cloud = get_cloud(session, str(identifier), user, write=True)
    if cloud.status != "ready" or not cloud.entry_file:
        raise HTTPException(409, "Chmura projektu nie jest gotowa do otwarcia.")
    return cloud


def project_view_cloud(session, project: Project) -> Cloud:
    """Use only after authorizing the project, independently of cloud visibility."""
    cloud = session.get(Cloud, project.cloud_id)
    if cloud is None or cloud.owner_id != project.owner_id or cloud.deleted_at is not None or cloud.status in ("deleted", "deleting"):
        raise HTTPException(404, "Chmura projektu jest niedostępna.")
    if cloud.status != "ready" or not cloud.entry_file:
        raise HTTPException(409, "Chmura projektu nie jest gotowa do otwarcia.")
    return cloud


def project_cloud_json(project: Project, cloud: Cloud, user, settings) -> dict:
    data = cloud_json(cloud, user, settings)
    owns = user is not None and project.owner_id == user.id
    data["infoUrl"] = f"/api/clouds/{cloud.id}/info" if owns else f"/api/projects/{project.id}/info"
    if not owns:
        data.update(url=f"/api/projects/{project.id}/files/{cloud.entry_file}",
                    viewerUrl=f"/viewer?project={project.id}", slug=str(cloud.id), parentCloud=None)
    return data


def project_json(project: Project, cloud: Cloud, user=None, *, detail=False) -> dict:
    data = {
        "id": str(project.id), "name": project.name, "cloudId": str(cloud.id), "cloudName": cloud.name,
        "cloudAvailable": cloud.deleted_at is None and cloud.status == "ready" and bool(cloud.entry_file),
        "viewerUrl": f"/viewer?project={project.id}", "cloudUrl": f"/api/projects/{project.id}/cloud",
        "visibility": project.visibility, "canEdit": user is not None and project.owner_id == user.id,
        "revision": project.revision,
        "createdAt": iso_utc(project.created_at), "updatedAt": iso_utc(project.updated_at),
    }
    if detail:
        data["sceneState"] = project.scene_state
    return data
