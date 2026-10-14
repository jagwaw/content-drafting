"""The data shapes that flow through the pipeline: Row in, Draft from the model, RowResult out."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Status(StrEnum):
    NEW = "New"
    DRAFTED = "Drafted"
    FLAGGED = "Flagged"
    ERROR = "Error"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class Row(BaseModel):
    """One `New` row read from the review sheet. Every text field is untrusted input."""

    model_config = ConfigDict(frozen=True)

    # Position in the backend (sheet row number or CSV line), so rows without an id
    # can still be written back to the right place.
    row_key: int
    id: str = ""
    topic: str = ""
    audience: str = ""
    tone: str = ""
    notes: str = ""


class Draft(BaseModel):
    """Generated content for one topic. Also the structured-output schema for the LLM."""

    model_config = ConfigDict(frozen=True)

    caption: str = Field(description="Social media caption.")
    slides: list[str] = Field(description="Carousel slides in order, one string per slide.")
    script: str = Field(description="Short video script, spoken words only.")


class RowResult(BaseModel):
    """The outcome for one row: what gets written back and audited."""

    model_config = ConfigDict(frozen=True)

    row: Row
    status: Status
    draft: Draft | None = None
    flags: tuple[str, ...] = ()
    model: str
    drafted_at: datetime
    error: str = ""

    @model_validator(mode="after")
    def _status_matches_content(self) -> Self:
        if self.status not in {Status.DRAFTED, Status.FLAGGED, Status.ERROR}:
            raise ValueError(f"the agent never sets status {self.status.value!r}")
        if (self.status == Status.DRAFTED) != (self.draft is not None):
            raise ValueError("a draft is present exactly when status is Drafted")
        if (self.status == Status.ERROR) != bool(self.error):
            raise ValueError("an error message is present exactly when status is Error")
        return self
