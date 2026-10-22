from uuid import UUID

from fastapi import Depends, Request, Response
from filelock import FileLock
from sqlalchemy import select

from .auth import current_user
from .collection_schemas import (CollectionAddClouds, CollectionCreate, CollectionList,
                                 CollectionRead, CollectionRename, CollectionSuggestions)
from .collections import add_collection_clouds, collection_json, collection_members, collection_suggestions, get_collection
from .models import CloudCollection, CollectionCloud, utcnow


def register_collection_routes(app, settings, get_session):
    def detail(session, collection, user):
        return collection_json(collection, collection_members(session, [collection.id], user)[collection.id])

    @app.get("/api/collections", response_model=CollectionList)
    def list_collections(request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        collections = list(session.scalars(select(CloudCollection).where(CloudCollection.owner_id == user.id)
                                          .order_by(CloudCollection.created_at, CloudCollection.id)))
        members = collection_members(session, [item.id for item in collections], user)
        return {"collections": [collection_json(item, members[item.id]) for item in collections]}

    @app.post("/api/collections", response_model=CollectionRead, status_code=201)
    def create_collection(body: CollectionCreate, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            collection = CloudCollection(owner_id=user.id, name=body.name)
            session.add(collection)
            session.flush()
            add_collection_clouds(session, collection, body.cloudIds, user)
            session.commit()
            return detail(session, collection, user)

    @app.get("/api/collections/{identifier}", response_model=CollectionRead)
    def read_collection(identifier: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        return detail(session, get_collection(session, identifier, user), user)

    @app.patch("/api/collections/{identifier}", response_model=CollectionRead)
    def rename_collection(identifier: UUID, body: CollectionRename, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            collection = get_collection(session, identifier, user)
            collection.name = body.name
            session.commit()
            return detail(session, collection, user)

    @app.delete("/api/collections/{identifier}", status_code=204)
    def delete_collection(identifier: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            session.delete(get_collection(session, identifier, user))
            session.commit()
        return Response(status_code=204)

    @app.post("/api/collections/{identifier}/clouds", response_model=CollectionRead)
    def add_clouds(identifier: UUID, body: CollectionAddClouds, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            collection = get_collection(session, identifier, user)
            add_collection_clouds(session, collection, body.cloudIds, user)
            session.commit()
            return detail(session, collection, user)

    @app.delete("/api/collections/{identifier}/clouds/{cloud_id}", response_model=CollectionRead)
    def remove_cloud(identifier: UUID, cloud_id: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        with FileLock(str(settings.data_dir / "cloud-mutations.lock"), timeout=30):
            collection = get_collection(session, identifier, user)
            membership = session.get(CollectionCloud, (collection.id, cloud_id))
            if membership:
                session.delete(membership)
                collection.updated_at = utcnow()
            session.commit()
            return detail(session, collection, user)

    @app.get("/api/collections/{identifier}/suggestions", response_model=CollectionSuggestions)
    def suggestions(identifier: UUID, request: Request, session=Depends(get_session)):
        user = current_user(request, session)
        collection = get_collection(session, identifier, user)
        return {"suggestions": collection_suggestions(session, collection, user)}
