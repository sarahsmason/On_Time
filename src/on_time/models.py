"""Domain model for On_Time (lights only). See the "How each light time is calculated" section of the README.

effective_time = base_time (clock or sunrise/sunset) + dst_glide + learned_offset
"""

from datetime import datetime, time
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]  # Monday=0 ... Sunday=6, as in datetime.weekday()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class LightAction(StrEnum):
    ON = "on"
    OFF = "off"


class LocationSource(StrEnum):
    DEVICE = "device"  # passed in by the Alexa+ (or simulated) host
    USER = "user"  # said or typed by the user


class Location(BaseModel):
    name: str = Field(description="Human-readable place, e.g. 'Oswego, NY'")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(description="IANA timezone, e.g. 'America/New_York'")
    source: LocationSource

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown IANA timezone: {value!r}") from e
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Light(BaseModel):
    id: str = Field(description="Stable id, e.g. 'living_room'")
    name: str
    is_on: bool = False


class ClockAnchor(BaseModel):
    """Fixed local clock time, e.g. wake-up at 06:30."""

    kind: Literal["clock"] = "clock"
    at: time


class SolarAnchor(BaseModel):
    """Sunrise or sunset at the user's location, plus an offset (negative = before)."""

    kind: Literal["solar"] = "solar"
    event: Literal["sunrise", "sunset"]
    offset_minutes: int = Field(default=0, ge=-240, le=240)


Anchor = Annotated[ClockAnchor | SolarAnchor, Field(discriminator="kind")]


class Routine(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rt"))
    name: str
    light_ids: list[str] = Field(min_length=1)
    action: LightAction
    anchor: Anchor
    days: list[int] = Field(default_factory=lambda: list(ALL_DAYS), description="Weekdays, Monday=0")
    learned_offset_minutes: int = Field(default=0, ge=-180, le=180, description="Only set after a confirmed check-in")
    dst_glide: bool = Field(default=True, description="Ease this routine through DST changes (clock anchors only)")
    enabled: bool = True

    @field_validator("days")
    @classmethod
    def _valid_days(cls, value: list[int]) -> list[int]:
        if not value or any(d not in ALL_DAYS for d in value):
            raise ValueError("days must be a non-empty list of 0 (Mon) .. 6 (Sun)")
        return sorted(set(value))

    def runs_on(self, day: datetime) -> bool:
        return self.enabled and day.weekday() in self.days


class Adjustment(BaseModel):
    """An ad-hoc light change by the user; the evidence check-ins are built from."""

    id: str = Field(default_factory=lambda: new_id("adj"))
    light_id: str
    action: LightAction
    at: AwareDatetime
    routine_id: str | None = Field(default=None, description="Routine this change deviated from, if any")
    scheduled_at: AwareDatetime | None = Field(default=None, description="When that routine was due")
    source: Literal["voice", "app", "seed"] = "voice"

    @property
    def delta_minutes(self) -> float | None:
        """Minutes relative to the scheduled time (negative = earlier than scheduled)."""
        if self.scheduled_at is None:
            return None
        return (self.at - self.scheduled_at).total_seconds() / 60


class CheckInKind(StrEnum):
    LEARNED_OFFSET = "learned_offset"  # shift a routine by a confirmed pattern
    ANCHOR_SWITCH = "anchor_switch"  # e.g. clock -> follow sunset
    DST_GLIDE = "dst_glide"  # ease routines through an upcoming DST change


class CheckInStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    ADJUSTED = "adjusted"
    DECLINED = "declined"


class CheckIn(BaseModel):
    """A conversational question the assistant raises at the next natural moment."""

    id: str = Field(default_factory=lambda: new_id("ci"))
    kind: CheckInKind
    routine_id: str | None = None
    question: str = Field(description="What the assistant asks, in plain language")
    evidence: list[str] = Field(default_factory=list, description="Why it's asking, e.g. '5 of last 7 evenings'")
    proposal: dict = Field(default_factory=dict, description="Change applied on accept, e.g. {'learned_offset_minutes': -20}")
    status: CheckInStatus = CheckInStatus.PENDING
    created_at: AwareDatetime
    resolved_at: AwareDatetime | None = None
    response_note: str | None = None
    muted_until: AwareDatetime | None = Field(default=None, description="Set on decline: don't re-ask before this")
