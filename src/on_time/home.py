"""Home service: location, lights, routines, schedule. MCP tools in server.py are thin wrappers over this."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from on_time import geo
from on_time.clock import Clock
from on_time.models import (
    Adjustment,
    Anchor,
    CheckIn,
    CheckInKind,
    CheckInStatus,
    Light,
    LightAction,
    Location,
    LocationSource,
    Routine,
)
from on_time.patterns import find_checkins
from on_time.schedule import (
    DstTransition,
    Glide,
    ScheduledEvent,
    ScheduleError,
    next_dst_transition,
    schedule_day,
    schedule_routine,
)
from on_time.storage import Repository

# A manual change within this many minutes of a routine's time counts as a deviation from it.
MATCH_WINDOW = timedelta(minutes=120)
# A declined check-in isn't asked again for this long.
CHECKIN_MUTE = timedelta(days=14)
# The accepted DST glide plan, applied by the scheduler.
ACTIVE_GLIDE_KEY = "active_dst_glide"


def _same_question(a: CheckIn, b: CheckIn) -> bool:
    return (
        a.kind == b.kind
        and a.routine_id == b.routine_id
        and a.proposal.get("transition_at") == b.proposal.get("transition_at")
    )


DEFAULT_LIGHTS = (
    Light(id="bedroom", name="Bedroom"),
    Light(id="living_room", name="Living room"),
    Light(id="kitchen", name="Kitchen"),
    Light(id="porch", name="Porch"),
)


class HomeError(ValueError):
    """An expected failure with a message meant for the user/agent."""


class Home:
    def __init__(self, repo: Repository, clock: Clock) -> None:
        self.repo = repo
        self.clock = clock

    def ensure_default_lights(self) -> None:
        """Create the simulated lights on first run."""
        if not self.repo.list_lights():
            for light in DEFAULT_LIGHTS:
                self.repo.upsert_light(light)

    # -- location --------------------------------------------------------------

    def get_location(self) -> Location | None:
        return self.repo.get_location()

    def find_city(self, city: str, region: str | None = None) -> list[geo.Place]:
        places = geo.find_cities(city, region)
        if not places:
            hint = f" in {region}" if region else ""
            raise HomeError(
                f"I couldn't find a city named {city!r}{hint}. Try a nearby larger city, or give latitude/longitude."
            )
        return places

    def set_location_from_place(self, place: geo.Place, source: LocationSource) -> Location:
        location = Location(
            name=place.label,
            latitude=place.latitude,
            longitude=place.longitude,
            timezone=place.timezone,
            source=source,
        )
        self.repo.set_location(location)
        return location

    def set_location_from_coordinates(
        self,
        latitude: float,
        longitude: float,
        source: LocationSource,
        name: str | None = None,
        timezone: str | None = None,
    ) -> Location:
        tz = timezone or geo.timezone_at(latitude, longitude)
        if tz is None:
            raise HomeError("I couldn't determine a timezone for those coordinates; please include the timezone.")
        location = Location(
            name=name or f"{latitude:.3f}, {longitude:.3f}",
            latitude=latitude,
            longitude=longitude,
            timezone=tz,
            source=source,
        )
        self.repo.set_location(location)
        return location

    # -- lights ----------------------------------------------------------------

    def list_lights(self) -> list[Light]:
        return self.repo.list_lights()

    def set_light(self, light_id: str, action: LightAction) -> tuple[Light, Adjustment]:
        """Ad-hoc on/off by the user. Logged as an adjustment (evidence for check-ins)."""
        light = self.repo.get_light(light_id)
        if light is None:
            valid = ", ".join(l.id for l in self.repo.list_lights())
            raise HomeError(f"Unknown light {light_id!r}. Valid lights: {valid}.")

        location = self.repo.get_location()
        now = self.clock.now(location.tz if location else None).replace(microsecond=0)
        light = light.model_copy(update={"is_on": action == LightAction.ON})
        self.repo.upsert_light(light)
        match = self.match_routine(light.id, action, now) if location else None
        adjustment = Adjustment(
            light_id=light.id,
            action=action,
            at=now,
            routine_id=match.routine.id if match else None,
            scheduled_at=match.at if match else None,
            source="voice",
        )
        self.repo.add_adjustment(adjustment)
        return light, adjustment

    def match_routine(self, light_id: str, action: LightAction, at: datetime) -> ScheduledEvent | None:
        """The routine (same light, same action) whose time today is nearest `at`, within MATCH_WINDOW."""
        location = self.repo.get_location()
        if location is None:
            return None
        candidates = [r for r in self.repo.list_routines() if light_id in r.light_ids and r.action == action]
        day = at.astimezone(location.tz).date()
        events, _ = schedule_day(candidates, day, location, location.tz, self.active_glide())
        nearby = [e for e in events if abs(e.at - at) <= MATCH_WINDOW]
        return min(nearby, key=lambda e: abs(e.at - at)) if nearby else None

    # -- routines --------------------------------------------------------------

    def _require_location(self) -> Location:
        location = self.repo.get_location()
        if location is None:
            raise HomeError("Set the home location first (ask the user where they live, or use the device location).")
        return location

    def add_routine(
        self, name: str, light_ids: list[str], action: LightAction, anchor: Anchor, days: list[int] | None = None
    ) -> Routine:
        known = {l.id for l in self.repo.list_lights()}
        unknown = [l for l in light_ids if l not in known]
        if unknown:
            raise HomeError(f"Unknown light(s): {', '.join(unknown)}. Valid lights: {', '.join(sorted(known))}.")
        routine = Routine(name=name, light_ids=light_ids, action=action, anchor=anchor, **({"days": days} if days else {}))
        self.repo.upsert_routine(routine)
        return routine

    def list_routines(self) -> list[Routine]:
        return self.repo.list_routines()

    def get_routine(self, routine_id: str) -> Routine:
        routine = self.repo.get_routine(routine_id)
        if routine is None:
            names = ", ".join(f"{r.id} ({r.name})" for r in self.repo.list_routines()) or "none yet"
            raise HomeError(f"Unknown routine {routine_id!r}. Routines: {names}.")
        return routine

    def remove_routine(self, routine_id: str) -> Routine:
        routine = self.get_routine(routine_id)
        self.repo.delete_routine(routine_id)
        return routine

    # -- schedule --------------------------------------------------------------

    def today(self) -> date:
        location = self._require_location()
        return self.clock.now(location.tz).date()

    def schedule_for(self, day: date) -> tuple[list[ScheduledEvent], list[str]]:
        location = self._require_location()
        return schedule_day(self.repo.list_routines(), day, location, location.tz, self.active_glide())

    def explain(self, routine_id: str, day: date) -> ScheduledEvent:
        location = self._require_location()
        routine = self.get_routine(routine_id)
        try:
            return schedule_routine(routine, day, location, location.tz, self.active_glide())
        except ScheduleError as e:
            raise HomeError(str(e)) from e

    def next_dst_change(self) -> tuple[ZoneInfo, DstTransition | None]:
        location = self._require_location()
        return location.tz, next_dst_transition(location.tz, self.clock.now(location.tz))

    # -- check-ins -------------------------------------------------------------

    def _now(self) -> datetime:
        location = self.repo.get_location()
        return self.clock.now(location.tz if location else None).replace(microsecond=0)

    def active_glide(self) -> Glide | None:
        raw = self.repo.get_setting(ACTIVE_GLIDE_KEY)
        return Glide.from_proposal(json.loads(raw)) if raw else None

    def pending_checkins(self) -> list[CheckIn]:
        """Pending check-ins, minus any about a routine that has since been removed."""
        routine_ids = {r.id for r in self.repo.list_routines()}
        return [
            c
            for c in self.repo.list_checkins(status=CheckInStatus.PENDING)
            if c.routine_id is None or c.routine_id in routine_ids
        ]

    def refresh_checkins(self) -> list[CheckIn]:
        """Run the detectors and queue anything new. Returns the check-ins that were newly raised."""
        location = self.repo.get_location()
        if location is None:
            return []
        now = self._now()
        last_changed: dict[str, datetime] = {}
        for c in self.repo.list_checkins():
            if c.routine_id and c.resolved_at and c.status in (CheckInStatus.ACCEPTED, CheckInStatus.ADJUSTED):
                last_changed[c.routine_id] = max(c.resolved_at, last_changed.get(c.routine_id, c.resolved_at))
        found = find_checkins(
            self.repo.list_routines(),
            self.repo.list_adjustments(since=now - timedelta(days=60)),
            location,
            now,
            next_dst_transition(location.tz, now),
            last_changed,
        )
        return [c for c in (self.raise_checkin(f) for f in found) if c is not None]

    def raise_checkin(self, checkin: CheckIn) -> CheckIn | None:
        """Queue a check-in unless the same question is pending, recently declined, or (for a DST change)
        already answered. Habit check-ins may be asked again later, but only on evidence gathered after
        the routine last changed (see patterns.find_checkins)."""
        now = self._now()
        for existing in self.repo.list_checkins():
            if not _same_question(existing, checkin):
                continue
            pending = existing.status == CheckInStatus.PENDING
            muted = existing.muted_until is not None and existing.muted_until > now
            settled_dst = existing.kind == CheckInKind.DST_GLIDE and existing.status in (
                CheckInStatus.ACCEPTED,
                CheckInStatus.ADJUSTED,
            )
            if pending or muted or settled_dst:
                return None
        self.repo.upsert_checkin(checkin)
        return checkin

    def respond_to_checkin(
        self, checkin_id: str, response: str, minutes: int | None = None, note: str | None = None
    ) -> CheckIn:
        checkin = self.repo.get_checkin(checkin_id)
        if checkin is None:
            raise HomeError(f"Unknown check-in {checkin_id!r}.")
        if checkin.status != CheckInStatus.PENDING:
            raise HomeError(f"That check-in was already answered ({checkin.status.value}).")

        now = self._now()
        if response == "decline":
            update = {"status": CheckInStatus.DECLINED, "muted_until": now + CHECKIN_MUTE}
        elif response in ("accept", "adjust"):
            if response == "adjust" and minutes is None:
                raise HomeError("To adjust, say how many minutes (e.g. -10 for ten minutes earlier).")
            self._apply(checkin, minutes if response == "adjust" else None)
            status = CheckInStatus.ADJUSTED if response == "adjust" else CheckInStatus.ACCEPTED
            update = {"status": status}
        else:
            raise HomeError("Respond with accept, adjust, or decline.")

        checkin = checkin.model_copy(update={**update, "resolved_at": now, "response_note": note})
        self.repo.upsert_checkin(checkin)
        return checkin

    def _apply(self, checkin: CheckIn, minutes: int | None) -> None:
        """Make the change a check-in proposed; `minutes` replaces the proposed amount when adjusting."""
        proposal = checkin.proposal
        if checkin.kind == CheckInKind.DST_GLIDE:
            if minutes is not None:
                raise HomeError("The Daylight Saving glide can be accepted or declined, not adjusted.")
            self.repo.set_setting(ACTIVE_GLIDE_KEY, json.dumps(proposal))
            return

        routine = self.get_routine(checkin.routine_id or "")
        if checkin.kind == CheckInKind.LEARNED_OFFSET:
            offset = minutes if minutes is not None else int(proposal["learned_offset_minutes"])
            changes: dict = {"learned_offset_minutes": offset}
        else:  # ANCHOR_SWITCH, e.g. clock -> follow sunset
            anchor = dict(proposal["anchor"])
            if minutes is not None:
                anchor["offset_minutes"] = minutes
            changes = {"anchor": anchor, "learned_offset_minutes": 0}
        try:
            updated = Routine.model_validate({**routine.model_dump(), **changes})
        except ValidationError as e:
            raise HomeError(f"That change isn't valid for the routine: {e.errors()[0]['msg']}.") from e
        self.repo.upsert_routine(updated)
