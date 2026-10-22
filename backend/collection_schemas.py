from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .schemas import CloudName


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CloudName
    cloudIds: Annotated[list[UUID], Field(max_length=1000)] = []


class CollectionRename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CloudName


class CollectionAddClouds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cloudIds: Annotated[list[UUID], Field(min_length=1, max_length=1000)]


class CollectionRead(BaseModel):
    id: UUID
    name: str
    cloudIds: list[UUID]
    createdAt: datetime
    updatedAt: datetime


class CollectionList(BaseModel):
    collections: list[CollectionRead]


class CollectionSuggestion(BaseModel):
    cloudId: UUID
    relatedCloudId: UUID
    relatedCloudName: str
    distance: int


class CollectionSuggestions(BaseModel):
    suggestions: list[CollectionSuggestion]
