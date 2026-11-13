"""Strict, minimal RAD1 HTTP DTOs. No clinical-finalization inputs."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Positive = Annotated[int, Field(strict=True, gt=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateExamBody(_Strict):
    imaging_order_id: Positive


class ScheduleIntentBody(_Strict):
    expected_version: Positive
    scheduled_at: AwareDatetime
    station_code: Annotated[str, Field(min_length=2, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]+$')]


class DraftReportBody(_Strict):
    content: Annotated[str, Field(min_length=1, max_length=20000)]


class ReviseReportBody(DraftReportBody):
    expected_version: Positive
