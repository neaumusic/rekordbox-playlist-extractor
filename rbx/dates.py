"""Pure timestamp redistribution algorithms used by expand-dates and reorder.

These have no rekordbox dependencies — they take plain dataclass items and return
new timestamps. Easy to unit-test for monotonicity, uniqueness, and resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum


class Resolution(StrEnum):
    MINUTE = "minute"
    SECOND = "second"

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=1) if self is Resolution.MINUTE else timedelta(seconds=1)

    @property
    def per_day(self) -> int:
        return 1440 if self is Resolution.MINUTE else 86400


@dataclass(frozen=True)
class ExpandItem:
    id: str
    current: date | datetime
    sort_key: int


@dataclass(frozen=True)
class ReorderItem:
    id: str
    current: date | datetime


def _to_datetime(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, time.min)


def _snap_to_resolution(d: datetime, resolution: Resolution) -> datetime:
    if resolution is Resolution.MINUTE:
        return d.replace(second=0, microsecond=0)
    return d.replace(microsecond=0)


def expand_within_day(
    items: list[ExpandItem],
    resolution: Resolution = Resolution.MINUTE,
) -> dict[str, datetime]:
    """Spread tracks sharing a day across unique timestamps within that day.

    Sorts each day-group by sort_key, assigns base + i*resolution. Skips groups
    whose timestamps are already all distinct at the given resolution
    (idempotency).

    Falls back to second precision automatically if a day has > 1440 items.
    Raises if a day has > 86400 items.
    """
    out: dict[str, datetime] = {}

    by_day: dict[date, list[ExpandItem]] = {}
    for item in items:
        day = _to_datetime(item.current).date()
        by_day.setdefault(day, []).append(item)

    for day, group in by_day.items():
        if len(group) == 1:
            out[group[0].id] = _snap_to_resolution(_to_datetime(group[0].current), resolution)
            continue

        group_sorted = sorted(group, key=lambda i: i.sort_key)

        if _already_unique(group_sorted, resolution):
            for it in group_sorted:
                out[it.id] = _snap_to_resolution(_to_datetime(it.current), resolution)
            continue

        active_resolution = resolution
        if (
            len(group_sorted) > active_resolution.per_day
            and active_resolution is Resolution.MINUTE
        ):
            active_resolution = Resolution.SECOND
        if len(group_sorted) > active_resolution.per_day:
            raise ValueError(
                f"Day {day} has {len(group_sorted)} tracks, exceeding capacity "
                f"({active_resolution.per_day}) even at second precision."
            )

        base = datetime.combine(day, time.min)
        step = active_resolution.delta
        for i, it in enumerate(group_sorted):
            out[it.id] = base + step * i

    return out


def _already_unique(group: list[ExpandItem], resolution: Resolution) -> bool:
    """True if every item's snapped timestamp is unique at this resolution."""
    seen: set[datetime] = set()
    for it in group:
        snapped = _snap_to_resolution(_to_datetime(it.current), resolution)
        if snapped in seen:
            return False
        seen.add(snapped)
    return True


def redistribute_range(
    items_in_order: list[ReorderItem],
    resolution: Resolution = Resolution.MINUTE,
) -> dict[str, datetime]:
    """Given items in the *desired* order with their current timestamps, return
    new timestamps that:

    - Order strictly matches the input order.
    - Range is anchored to the original [min(current)..max(current)] window when
      possible; expanded symmetrically around the midpoint when not.
    - Snapped to `resolution`; strictly monotonic; unique.
    """
    n = len(items_in_order)
    if n == 0:
        return {}
    if n == 1:
        only = items_in_order[0]
        return {only.id: _snap_to_resolution(_to_datetime(only.current), resolution)}

    currents = [_to_datetime(i.current) for i in items_in_order]
    lo, hi = min(currents), max(currents)
    step = resolution.delta
    required_span = step * (n - 1)
    actual_span = hi - lo

    if actual_span >= required_span:
        new_lo = _snap_to_resolution(lo, resolution)
        new_hi = _snap_to_resolution(hi, resolution)
    else:
        midpoint = lo + actual_span / 2
        new_lo = _snap_to_resolution(midpoint - required_span / 2, resolution)
        new_hi = new_lo + required_span

    base_step_seconds = (new_hi - new_lo).total_seconds() / (n - 1)
    out: dict[str, datetime] = {}
    last: datetime | None = None
    for i, item in enumerate(items_in_order):
        if i == 0:
            ts = new_lo
        elif i == n - 1:
            ts = new_hi
        else:
            ts = new_lo + timedelta(seconds=base_step_seconds * i)
            ts = _snap_to_resolution(ts, resolution)

        if last is not None and ts <= last:
            ts = last + step
        out[item.id] = ts
        last = ts
    return out
