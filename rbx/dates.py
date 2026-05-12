"""Pure timestamp redistribution algorithms used by expand-dates and sort.

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


@dataclass(frozen=True)
class ExpandItem:
    id: str
    current: date | datetime
    sort_key: int
    tiebreaker_dt: datetime | None = None
    """High-resolution timestamp used to order items that share a `current` value.

    Concrete use: rekordbox stores `StockDate` (the user-facing "Date Added") as
    a date-only VARCHAR, but its UI breaks ties using the SQLite row's
    `created_at` column (millisecond precision). Pass `created_at` here so the
    algorithm reproduces rekordbox's natural display order. Falls back to
    `sort_key` alone when None.
    """


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


def expand_unique_backward(
    items: list[ExpandItem],
    resolution: Resolution = Resolution.MINUTE,
) -> dict[str, datetime]:
    """Assign every item a unique timestamp at `resolution`, preserving the
    natural order (raw current-timestamp, then tiebreaker_dt, then sort_key).

    Algorithm: stable-sort ascending by (raw_timestamp, tiebreaker_dt, sort_key).
    The raw timestamp carries any sub-resolution precision present in `current`
    (e.g. seconds when assigning at minute resolution). When `current` itself
    is date-only and many items share a day, `tiebreaker_dt` (e.g. the SQLite
    `created_at` column) restores the rekordbox-UI order. `sort_key` is the
    final deterministic fallback. Snapping happens only during the assignment
    pass, never during ordering.

    Then sweep backward from the latest item: each item keeps its snapped
    timestamp if that's strictly less than the previously-assigned one;
    otherwise it gets pushed back by one resolution step. Cascades freely
    cross day boundaries.

    Properties:
    - The latest items in the sort order are anchored at their snapped values.
    - Earlier colliders shift backward by N*step where N is the cascade length.
    - Sub-resolution ordering is preserved when items snap to the same slot
      (the later raw timestamp anchors; the earlier gets pushed back).
    - Output is deterministic given the (raw_timestamp, tiebreaker_dt, sort_key)
      ordering.
    - Idempotent: re-running on the output yields no further changes.
    - O(n log n) total.

    There is no per-day cap; cross-day spillover is the explicit design choice.
    The only theoretical failure mode is exhausting Python's datetime range,
    which would require billions of colliding tracks.
    """
    if not items:
        return {}

    step = resolution.delta
    sorted_items = sorted(
        items,
        key=lambda it: (
            _to_datetime(it.current),
            it.tiebreaker_dt or datetime.min,
            it.sort_key,
        ),
    )

    out: dict[str, datetime] = {}
    next_allowed: datetime | None = None
    for it in reversed(sorted_items):
        snapped = _snap_to_resolution(_to_datetime(it.current), resolution)
        if next_allowed is None or snapped < next_allowed:
            assigned = snapped
        else:
            assigned = next_allowed - step
        out[it.id] = assigned
        next_allowed = assigned

    return out


def pack_for_sort(
    m3u_items_in_order: list[ReorderItem],
    other_library_items: list[ExpandItem],
    resolution: Resolution = Resolution.MINUTE,
) -> dict[str, datetime]:
    """Anchor on the LAST m3u entry; pack earlier m3u entries backward at one
    `resolution`-step intervals; cascade colliding non-m3u library tracks
    earlier until every date is globally unique.

    Convention: m3u top = oldest, m3u bottom = newest (the anchor). Same
    convention `import` writes — so `import` -> `sort` round-trips with no
    reordering.

    Algorithm:
    1. Snap the last m3u item's current StockDate; that's the anchor. It keeps
       this date. Earlier m3u items get anchor - step, anchor - 2*step, ...
    2. Process every other library item in reverse-chronological order of its
       snapped current date (sort_key as tiebreaker). For each one:
         - candidate = snapped current
         - while candidate is occupied (by m3u or a previously-placed item),
           candidate -= step
       This means non-m3u items OUTSIDE the m3u-affected window are not
       disturbed at all. Items INSIDE the window step backward through it
       until they find a free slot.

    Order between cascaded non-m3u items is preserved naturally: we process
    latest-first, and each item steps past previously-claimed slots, so the
    item with the later original date lands at a later final slot.

    Note: order between cascaded items and items that didn't need to cascade
    can flip — a track originally newer than the anchor that gets cascaded
    way back may end up earlier than tracks that were originally older but
    outside the m3u window. The trade-off is "preserve each non-m3u item's
    original date when possible" rather than "preserve all relative ordering".

    Output only contains items whose date actually needs to change. m3u items
    are always present. Non-m3u items are present only if cascading moved
    them off their snapped current date.

    Strongly recommended to run `expand_unique_backward` on the full library
    first (i.e., `rbx expand-dates`) so non-m3u items don't have pre-existing
    sub-resolution collisions that would also get rearranged here.
    """
    if not m3u_items_in_order:
        return {}

    step = resolution.delta

    anchor_dt = _snap_to_resolution(
        _to_datetime(m3u_items_in_order[-1].current), resolution
    )
    m3u_dates: dict[str, datetime] = {}
    for offset, item in enumerate(reversed(m3u_items_in_order)):
        m3u_dates[item.id] = anchor_dt - step * offset

    occupied: set[datetime] = set(m3u_dates.values())
    out: dict[str, datetime] = dict(m3u_dates)

    m3u_id_set = {it.id for it in m3u_items_in_order}
    non_m3u = [it for it in other_library_items if it.id not in m3u_id_set]
    non_m3u.sort(
        key=lambda it: (
            _snap_to_resolution(_to_datetime(it.current), resolution),
            it.tiebreaker_dt or datetime.min,
            it.sort_key,
        ),
        reverse=True,
    )

    for item in non_m3u:
        snapped = _snap_to_resolution(_to_datetime(item.current), resolution)
        candidate = snapped
        while candidate in occupied:
            candidate -= step
        if candidate != snapped:
            out[item.id] = candidate
        occupied.add(candidate)

    return out
