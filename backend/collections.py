"""Private cloud membership and suggestions from the owner's processing graph."""
from collections import defaultdict, deque
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Cloud, CloudCollection, CollectionCloud, User, iso_utc, utcnow


def get_collection(session: Session, identifier: UUID, user: User) -> CloudCollection:
    collection = session.get(CloudCollection, identifier)
    if collection is None or collection.owner_id != user.id:
        raise HTTPException(404, "Nie znaleziono kolekcji.")
    return collection


def collection_members(session: Session, collection_ids: list[UUID], user: User) -> dict:
    members = defaultdict(list)
    if collection_ids:
        rows = session.execute(select(CollectionCloud.collection_id, Cloud.id).join(
            Cloud, Cloud.id == CollectionCloud.cloud_id).where(
                CollectionCloud.collection_id.in_(collection_ids), Cloud.owner_id == user.id,
                Cloud.deleted_at.is_(None), Cloud.status.not_in(("deleting", "deleted")))
            .order_by(Cloud.created_at.desc(), Cloud.id))
        for collection_id, cloud_id in rows:
            members[collection_id].append(cloud_id)
    return members


def collection_json(collection: CloudCollection, cloud_ids: list[UUID]) -> dict:
    return {"id": str(collection.id), "name": collection.name,
            "description": collection.description, "notes": collection.notes,
            "cloudIds": [str(identifier) for identifier in cloud_ids],
            "createdAt": iso_utc(collection.created_at), "updatedAt": iso_utc(collection.updated_at)}


def add_collection_clouds(session: Session, collection: CloudCollection, cloud_ids: list[UUID], user: User) -> None:
    """Validate the entire batch before changing membership; repeated adds are idempotent."""
    requested = set(cloud_ids)
    if not requested:
        return
    allowed = set(session.scalars(select(Cloud.id).where(
        Cloud.id.in_(requested), Cloud.owner_id == user.id, Cloud.deleted_at.is_(None),
        Cloud.status.not_in(("deleting", "deleted")))))
    if requested != allowed:
        raise HTTPException(404, "Nie znaleziono jednej z chmur w Twojej bibliotece.")
    existing = set(session.scalars(select(CollectionCloud.cloud_id).where(
        CollectionCloud.collection_id == collection.id, CollectionCloud.cloud_id.in_(requested))))
    for identifier in requested - existing:
        session.add(CollectionCloud(collection_id=collection.id, cloud_id=identifier))
    if requested - existing:
        collection.updated_at = utcnow()


def collection_suggestions(session: Session, collection: CloudCollection, user: User) -> list[dict]:
    members = set(collection_members(session, [collection.id], user)[collection.id])
    if not members:
        return []
    # Retained deleted records may connect surviving siblings, but are never returned.
    rows = session.execute(select(Cloud.id, Cloud.parent_id, Cloud.name, Cloud.status, Cloud.deleted_at)
                           .where(Cloud.owner_id == user.id)).all()
    clouds = {row.id: row for row in rows}
    edges = defaultdict(set)
    for row in rows:
        if row.parent_id in clouds:
            edges[row.id].add(row.parent_id)
            edges[row.parent_id].add(row.id)
    visited = set(members)
    queue = deque((identifier, identifier, 0) for identifier in sorted(members))
    suggestions = []
    while queue:
        current, related, distance = queue.popleft()
        for identifier in sorted(edges[current]):
            if identifier in visited:
                continue
            visited.add(identifier)
            queue.append((identifier, related, distance + 1))
            row = clouds[identifier]
            if row.deleted_at is None and row.status not in ("deleting", "deleted"):
                suggestions.append({"cloudId": str(identifier), "relatedCloudId": str(related),
                                    "relatedCloudName": clouds[related].name, "distance": distance + 1})
    return sorted(suggestions, key=lambda item: (
        item["distance"], clouds[UUID(item["cloudId"])].name.casefold(), item["cloudId"]))
