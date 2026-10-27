"""Owner-only processing families, without reading point files or operation payloads."""
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Cloud, ProcessingJob, User, iso_utc

MAX_LINEAGE_NODES = 1000


def has_cycle(clouds: dict, identifiers: set) -> bool:
    finished = set()
    for identifier in identifiers:
        path = set()
        while identifier in identifiers and identifier not in finished:
            if identifier in path:
                return True
            path.add(identifier)
            identifier = clouds[identifier].parent_id
        finished.update(path)
    return False


def cloud_lineage(session: Session, cloud: Cloud, user: User) -> dict:
    # Only the operation label is needed; large clip selections stay in the database.
    job_kind = (select(ProcessingJob.kind).where(
        ProcessingJob.cloud_id == Cloud.id, ProcessingJob.owner_id == user.id)
        .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id).limit(1).scalar_subquery())
    rows = session.execute(select(
        Cloud.id, Cloud.parent_id, Cloud.name, Cloud.status, Cloud.deleted_at,
        Cloud.created_at, Cloud.entry_file,
        Cloud.operation["type"].as_string().label("operation_type"), job_kind.label("job_kind"))
        .where(Cloud.owner_id == user.id).order_by(Cloud.created_at, Cloud.id)).all()
    known = {row.id: row for row in rows}
    edges = defaultdict(list)
    for row in rows:
        if row.parent_id in known:
            edges[row.id].append(row.parent_id)
            edges[row.parent_id].append(row.id)
    queue = deque([cloud.id])
    included = {cloud.id}
    truncated = False
    while queue:
        for identifier in edges[queue.popleft()]:
            if identifier in included:
                continue
            if len(included) >= MAX_LINEAGE_NODES:
                truncated = True
                continue
            included.add(identifier)
            queue.append(identifier)

    def ready(row):
        return row is not None and row.deleted_at is None and row.status == "ready" and bool(row.entry_file)

    nodes = []
    for row in rows:
        if row.id not in included:
            continue
        deleted = row.deleted_at is not None or row.status in ("deleting", "deleted")
        parent = known.get(row.parent_id)
        operation_type = row.operation_type if row.operation_type in (
            "import", "segmentation", "transformation", "point-removal") else None
        nodes.append({
            "id": str(row.id), "parentId": str(row.parent_id) if row.parent_id in included else None,
            "name": row.name, "status": "deleted" if deleted else row.status,
            "operationType": operation_type or row.job_kind or ("processing" if row.parent_id else "import"),
            "createdAt": iso_utc(row.created_at), "canInspect": not deleted,
            "sourceUnavailable": row.parent_id is not None and row.parent_id not in included,
            "viewerUrl": f"/viewer?cloud={row.id}" if ready(row) else None,
            "comparisonUrl": f"/compare?cloud={row.id}" if row.parent_id != row.id and ready(row) and ready(parent) else None,
        })
    notices = []
    if truncated:
        notices.append(f"Pokazano do {MAX_LINEAGE_NODES} chmur najbliżej wybranej chmury. Rodzina przetwarzania jest większa.")
    if any(node["sourceUnavailable"] for node in nodes):
        notices.append("Część źródeł jest niedostępna lub znajduje się poza pokazanym fragmentem historii.")
    if has_cycle(known, included):
        notices.append("W zapisanych powiązaniach wykryto cykl. Każda chmura jest pokazana tylko raz.")
    return {"cloudId": str(cloud.id), "nodes": nodes, "truncated": truncated, "notices": notices}
