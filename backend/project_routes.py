from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response
from filelock import FileLock
from sqlalchemy import select, update
from sqlalchemy.orm import defer

from .auth import current_user
from .models import Cloud, Project, utcnow
from .project_schemas import ProjectCreate, ProjectList, ProjectRead, ProjectSummary, ProjectUpdate, ProjectVisibilityUpdate
from .projects import get_project, project_cloud, project_json, project_view_cloud


def register_project_routes(app, settings, get_session):
    @app.get("/api/projects", response_model=ProjectList)
    def list_projects(request: Request, cloudId: UUID | None = None, session=Depends(get_session)):
        user = current_user(request, session)
        query = select(Project, Cloud).options(defer(Project.scene_state)).join(Cloud, Project.cloud_id == Cloud.id).where(
            Project.owner_id == user.id, Cloud.owner_id == user.id)
        if cloudId is not None:
            project_cloud(session, cloudId, user)
            query = query.where(Project.cloud_id == cloudId)
        records = session.execute(query.order_by(Project.updated_at.desc(), Project.id)).all()
        return {"projects": [project_json(project, cloud, user) for project, cloud in records]}

    @app.post("/api/projects", response_model=ProjectRead, status_code=201)
    def create_project(body: ProjectCreate, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            cloud = project_cloud(session, body.cloudId, user)
            project = Project(owner_id=user.id, cloud_id=cloud.id, name=body.name,
                              scene_state=body.sceneState.model_dump(mode="json"), visibility=body.visibility)
            session.add(project)
            session.commit()
        return project_json(project, cloud, user, detail=True)

    @app.get("/api/projects/{identifier}", response_model=ProjectRead)
    def read_project(identifier: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session, required=False)
        project = get_project(session, identifier, user, write=False)
        cloud = project_view_cloud(session, project)
        return project_json(project, cloud, user, detail=True)

    @app.put("/api/projects/{identifier}", response_model=ProjectRead)
    def save_project(identifier: UUID, body: ProjectUpdate, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            project = get_project(session, identifier, user)
            cloud = project_cloud(session, project.cloud_id, user)
            saved = session.execute(update(Project).where(
                Project.id == identifier, Project.owner_id == user.id, Project.revision == body.revision
            ).values(name=body.name, scene_state=body.sceneState.model_dump(mode="json"),
                     revision=Project.revision + 1, updated_at=utcnow()))
            if saved.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "Projekt zmieniono w innej karcie. Otwórz go ponownie lub zapisz kopię.")
            session.commit()
            session.refresh(project)
        return project_json(project, cloud, user, detail=True)

    @app.patch("/api/projects/{identifier}", response_model=ProjectSummary)
    def change_project_visibility(identifier: UUID, body: ProjectVisibilityUpdate, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            project = get_project(session, identifier, user)
            cloud = project_cloud(session, project.cloud_id, user) if body.visibility == "public" else session.get(Cloud, project.cloud_id)
            saved = session.execute(update(Project).where(
                Project.id == identifier, Project.owner_id == user.id, Project.revision == body.revision
            ).values(visibility=body.visibility, revision=Project.revision + 1, updated_at=utcnow()))
            if saved.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "Projekt zmieniono w innej karcie. Otwórz go ponownie, aby zmienić dostęp.")
            session.commit()
            session.refresh(project)
        return project_json(project, cloud, user)

    @app.delete("/api/projects/{identifier}", status_code=204)
    def delete_project(identifier: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        project = get_project(session, identifier, user)
        session.delete(project)
        session.commit()
        return Response(status_code=204)
