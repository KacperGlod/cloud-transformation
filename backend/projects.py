"""Access and serialization for private, single-cloud viewer projects."""
from uuid import UUID

from fastapi import HTTPException

from .models import Cloud, Project, iso_utc
from .services import get_cloud


def get_project(session, identifier: UUID, user) -> Project:
    project = session.get(Project, identifier)
    if project is None or project.owner_id != user.id:
        raise HTTPException(404, "Nie znaleziono projektu.")
    return project


def project_cloud(session, identifier, user) -> Cloud:
    cloud = get_cloud(session, str(identifier), user, write=True)
    if cloud.status != "ready" or not cloud.entry_file:
        raise HTTPException(409, "Chmura projektu nie jest gotowa do otwarcia.")
    return cloud


def project_json(project: Project, cloud: Cloud, *, detail=False) -> dict:
    data = {
        "id": str(project.id), "name": project.name, "cloudId": str(cloud.id), "cloudName": cloud.name,
        "cloudAvailable": cloud.deleted_at is None and cloud.status == "ready" and bool(cloud.entry_file),
        "viewerUrl": f"/viewer?project={project.id}", "revision": project.revision,
        "createdAt": iso_utc(project.created_at), "updatedAt": iso_utc(project.updated_at),
    }
    if detail:
        data["sceneState"] = project.scene_state
    return data
