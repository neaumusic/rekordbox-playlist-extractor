"""Pure ordering / redistribution algorithms used by expand-dates, sort, and shuffle.

These have no rekordbox dependencies — they take plain dataclass items and return
new values. Easy to unit-test for monotonicity, uniqueness, and resolution.

The algorithms work in any 1-D ordered "value space". Originally datetime-only
(for ``StockDate`` minute-precision packing); generalized so the same packing
logic powers ``ReleaseYear`` (integer 0..9999, where 0 = newest per the iOS
sort workaround) and zero-padded numeric Genre/Album names.

Convention used internally: every space exposes an integer-or-comparable axis
where **lower axis = older**. Concrete spaces decide how external values map
to axis units. Datetime axis is the snapped datetime (later = newer); the
integer / padded-int spaces invert (lower year = newer, so axis = -year).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any


class Resolution(StrEnum):
    MINUTE = "minute"
    SECOND = "second"
    DAY = "day"

    @property
    def delta(self) -> timedelta:
        if self is Resolution.MINUTE:
            return timedelta(minutes=1)
        if self is Resolution.SECOND:
            return timedelta(seconds=1)
        return timedelta(days=1)


@dataclass(frozen=True)
class ExpandItem:
    id: str
    current: Any
    sort_key: int
    tiebreaker_dt: datetime | None = None
    """High-resolution timestamp used to order items that share a `current` value.

    Concrete use: rekordbox stores `StockDate` (the user-facing "Date Added") as
    a date-only VARCHAR, but its UI breaks ties using the SQLite row's
    `created_at` column (millisecond precision). Pass `created_at` here so the
    algorithm reproduces rekordbox's natural display order. Falls back to
    `sort_key` alone when None.

    Only consulted by spaces that don't expose sub-axis precision themselves
    (e.g. integer/year). DateSpace breaks ties via the raw datetime first.
    """


@dataclass(frozen=True)
class ReorderItem:
    id: str
    current: Any


class ValueSpace(ABC):
    """Abstract sort axis used by ``expand_unique_backward_field`` and
    ``pack_for_sort_field``.

    Convention:

    - Axis values are hashable + totally ordered.
    - **Lower axis = older.** Concrete spaces invert their external value if
      needed (e.g. ``IntSpace`` for ReleaseYear uses axis = -year).
    - ``older(axis)`` returns the next slot in the older direction.
    - ``at_cap(axis)`` is True when cascade has hit the older cap of a finite
      space; overflow items stack at the cap value, sharing one output.
    - ``order_key`` returns a tiebreaker key for input sort (lower = older);
      may be finer than ``snap_axis`` when the space allows sub-axis precision
      (DateSpace preserves seconds even when packing at minute resolution).
    """

    @abstractmethod
    def snap_axis(self, value: Any) -> Any:
        """Snap an external value to its assignment axis position."""

    @abstractmethod
    def order_key(self, value: Any) -> Any:
        """Sub-axis-precision sort key for ordering input items. Lower = older."""

    @abstractmethod
    def older(self, axis: Any) -> Any:
        """One step older. May clamp at a cap; never raises."""

    @abstractmethod
    def at_cap(self, axis: Any) -> bool:
        """True when cascade has bottomed out at the older end of this space."""

    @abstractmethod
    def to_output(self, axis: Any) -> Any:
        """Render the axis value as the externally stored form."""

    def anchor_axis(self, m3u_items: list[ReorderItem]) -> Any:
        """Pick the axis position for the m3u anchor (last m3u item).

        Default: snap the last item's current value (what date-anchored sort
        has always done). Bounded spaces override to fix the anchor at one
        end (e.g. IntSpace anchors at min_value so the newest m3u track lands
        at year 0).
        """
        return self.snap_axis(m3u_items[-1].current)


class DateSpace(ValueSpace):
    """Datetime axis: axis = snapped datetime; lower datetime = older."""

    def __init__(self, resolution: Resolution = Resolution.MINUTE) -> None:
        self.resolution = resolution

    def snap_axis(self, value: Any) -> datetime:
        return _snap_to_resolution(_to_datetime(value), self.resolution)

    def order_key(self, value: Any) -> datetime:
        return _to_datetime(value)

    def older(self, axis: datetime) -> datetime:
        return axis - self.resolution.delta

    def at_cap(self, axis: datetime) -> bool:
        return False

    def to_output(self, axis: datetime) -> datetime:
        return axis


class IntSpace(ValueSpace):
    """Integer axis with a lower-is-newer convention (axis = -value).

    Used for ReleaseYear: ``min_value=0`` is newest, ``max_value=9999`` is the
    older cap. Items whose value is below ``min_value`` clamp to the anchor
    side; items beyond ``max_value`` (or that cascade past it) all share the
    cap output.
    """

    def __init__(self, min_value: int = 0, max_value: int = 9999) -> None:
        if max_value <= min_value:
            raise ValueError("max_value must be > min_value")
        self.min_value = min_value
        self.max_value = max_value

    def _clamp(self, value: int) -> int:
        return max(self.min_value, min(self.max_value, int(value)))

    def snap_axis(self, value: Any) -> int:
        return -self._clamp(value)

    def order_key(self, value: Any) -> int:
        return -self._clamp(value)

    def older(self, axis: int) -> int:
        # Older direction = higher value = lower axis. Cap at -max_value.
        return max(axis - 1, -self.max_value)

    def at_cap(self, axis: int) -> bool:
        return axis <= -self.max_value

    def to_output(self, axis: int) -> int:
        return self._clamp(-axis)

    def anchor_axis(self, m3u_items: list[ReorderItem]) -> int:
        return -self.min_value


class PaddedIntSpace(IntSpace):
    """IntSpace wrapper that emits zero-padded numeric strings.

    Used for Genre/Album where iOS sorts lexicographically; padding to a fixed
    width makes the namespace sort numerically. ``snap_axis`` accepts ints and
    numeric strings; non-numeric strings raise (callers should pre-filter).
    """

    def __init__(
        self,
        width: int,
        min_value: int = 0,
        max_value: int | None = None,
    ) -> None:
        if max_value is None:
            max_value = 10**width - 1
        super().__init__(min_value=min_value, max_value=max_value)
        self.width = width

    def _coerce(self, value: Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value)
        raise TypeError(f"PaddedIntSpace.snap_axis expected int or numeric str, got {value!r}")

    def snap_axis(self, value: Any) -> int:
        return -self._clamp(self._coerce(value))

    def order_key(self, value: Any) -> int:
        return -self._clamp(self._coerce(value))

    def to_output(self, axis: int) -> str:
        return f"{self._clamp(-axis):0{self.width}d}"


def _to_datetime(d: Any) -> datetime:
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime.combine(d, time.min)
    raise TypeError(f"expected date or datetime, got {d!r}")


def _snap_to_resolution(d: datetime, resolution: Resolution) -> datetime:
    if resolution is Resolution.MINUTE:
        return d.replace(second=0, microsecond=0)
    if resolution is Resolution.SECOND:
        return d.replace(microsecond=0)
    return datetime.combine(d.date(), time.min)


def expand_unique_backward_field(
    items: list[ExpandItem],
    space: ValueSpace,
) -> dict[str, Any]:
    """Generic version of ``expand_unique_backward`` parameterised by a space.

    Same algorithm as the date-only version: stable-sort ascending by
    ``(order_key, tiebreaker_dt, sort_key)`` then sweep backward from the
    newest, pushing each colliding item to ``space.older(prev)``. Output is
    a mapping from item.id to ``space.to_output(axis)``.
    """
    if not items:
        return {}

    sorted_items = sorted(
        items,
        key=lambda it: (
            space.order_key(it.current),
            it.tiebreaker_dt or datetime.min,
            it.sort_key,
        ),
    )

    out: dict[str, Any] = {}
    next_allowed: Any = None
    for it in reversed(sorted_items):
        snapped = space.snap_axis(it.current)
        if next_allowed is None or snapped < next_allowed:
            assigned = snapped
        else:
            assigned = space.older(next_allowed)
        out[it.id] = space.to_output(assigned)
        next_allowed = assigned

    return out


def expand_unique_backward(
    items: list[ExpandItem],
    resolution: Resolution = Resolution.MINUTE,
) -> dict[str, datetime]:
    """Assign every item a unique datetime at ``resolution``, preserving the
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
    return expand_unique_backward_field(items, DateSpace(resolution))


def pack_for_sort_field(
    m3u_items_in_order: list[ReorderItem],
    other_library_items: list[ExpandItem],
    space: ValueSpace,
) -> dict[str, Any]:
    """Generic version of ``pack_for_sort`` parameterised by a space.

    1. Use ``space.anchor_axis(m3u_items_in_order)`` for the m3u anchor; pack
       earlier m3u entries backward via ``space.older`` at one step per item.
    2. For every non-m3u item, snap its current value and cascade via
       ``space.older`` past any occupied axis. Items at the older cap are
       allowed to share the cap output without further cascade.

    The output only contains items whose stored value actually changes.
    """
    if not m3u_items_in_order:
        return {}

    anchor = space.anchor_axis(m3u_items_in_order)
    m3u_axes: dict[str, Any] = {}
    cursor = anchor
    for item in reversed(m3u_items_in_order):
        m3u_axes[item.id] = cursor
        cursor = space.older(cursor)

    occupied: set[Any] = {ax for ax in m3u_axes.values() if not space.at_cap(ax)}
    out: dict[str, Any] = {iid: space.to_output(ax) for iid, ax in m3u_axes.items()}

    m3u_id_set = {it.id for it in m3u_items_in_order}
    non_m3u = [it for it in other_library_items if it.id not in m3u_id_set]
    non_m3u.sort(
        key=lambda it: (
            space.order_key(it.current),
            it.tiebreaker_dt or datetime.min,
            it.sort_key,
        ),
        reverse=True,
    )

    for item in non_m3u:
        snapped = space.snap_axis(item.current)
        candidate = snapped
        while candidate in occupied and not space.at_cap(candidate):
            candidate = space.older(candidate)
        if candidate != snapped:
            out[item.id] = space.to_output(candidate)
        if not space.at_cap(candidate):
            occupied.add(candidate)

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
    return pack_for_sort_field(m3u_items_in_order, other_library_items, DateSpace(resolution))
