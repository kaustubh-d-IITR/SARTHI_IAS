"""Request and response models for the studio API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthConfiguration(BaseModel):
    max_file_bytes: int
    max_session_bytes: int
    allowed_extensions: list[str]
    ffprobe_available: bool
    credentials_loaded: bool = False
    auth_mode: str = "client_header"
    upload_mode: str = "direct"
    environment: str = "development"


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    upload_directory_writable: bool
    configuration: HealthConfiguration


class RawUploadResponse(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    content_type: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None = None
    validation_status: Literal["valid"]


class BrollUploadResponse(RawUploadResponse):
    asset_id: str
    description: str


class BrollRef(BaseModel):
    asset_id: str
    description: str = Field(min_length=1, max_length=500)


class SessionCreate(BaseModel):
    mode: Literal["auto_edit", "my_broll"]
    raw_video_id: str
    broll: list[BrollRef] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=200)


class PlanEdit(BaseModel):
    base_version_id: str
    placements: list[dict]
    title: str | None = None
    change_title: bool = False


class SessionAsset(BaseModel):
    asset_id: str
    upload_id: str
    filename: str
    description: str
    size_bytes: int
    content_type: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None = None
    validation_status: str


SessionStatus = Literal[
    "prepared",
    "queued",
    "transcribing",
    "analysing",
    "analysing_footage",
    "selecting_footage",
    "building_plan",
    "validating_plan",
    "plan_ready",
    "failed",
]


class SessionResponse(BaseModel):
    session_id: str
    mode: Literal["auto_edit", "my_broll"]
    status: SessionStatus
    stage: str = ""
    error: str = ""
    raw_video: RawUploadResponse
    broll: list[SessionAsset]
    total_bytes: int
    broll_count: int
    note: str
    created_at: str
    render: dict | None = None
    title: dict | None = None
    plan_version: str | None = None
