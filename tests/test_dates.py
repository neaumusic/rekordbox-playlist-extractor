"""Tests for the timestamp redistribution algorithms."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from rbx.dates import (
    ExpandItem,
    IntSpace,
    PaddedIntSpace,
    ReorderItem,
    Resolution,
    expand_unique_backward,
    expand_unique_backward_field,
    pack_for_sort,
    pack_for_sort_field,
)


class TestExpandUniqueBackward:
    def test_empty_input_returns_empty(self) -> None:
        assert expand_unique_backward([]) == {}

    def test_single_item_passthrough(self) -> None:
        items = [ExpandItem("a", date(2020, 1, 1), sort_key=1)]
        assert expand_unique_backward(items) == {"a": datetime(2020, 1, 1)}

    def test_already_unique_timestamps_are_preserved(self) -> None:
        items = [
            ExpandItem("a", datetime(2020, 1, 1, 10, 0), sort_key=1),
            ExpandItem("b", datetime(2020, 1, 1, 14, 30), sort_key=2),
        ]
        result = expand_unique_backward(items)
        assert result["a"] == datetime(2020, 1, 1, 10, 0)
        assert result["b"] == datetime(2020, 1, 1, 14, 30)

    def test_sub_minute_differences_collide_and_push_earlier_item_back(self) -> None:
        items = [
            ExpandItem("a", datetime(2020, 1, 1, 10, 0, 5), sort_key=1),
            ExpandItem("b", datetime(2020, 1, 1, 10, 0, 30), sort_key=2),
        ]
        result = expand_unique_backward(items)
        assert result["b"] == datetime(2020, 1, 1, 10, 0)
        assert result["a"] == datetime(2020, 1, 1, 9, 59)

    def test_raw_timestamp_order_wins_when_sort_key_disagrees(self) -> None:
        """If two tracks snap to the same slot, the one with the later raw
        timestamp (sub-resolution precision) must anchor — even when its
        sort_key is lower. This protects the rekordbox-UI order in cases
        where StockDate happens to carry sub-resolution precision.
        """
        items = [
            ExpandItem("a_later_raw", datetime(2020, 1, 1, 14, 23, 45), sort_key=1),
            ExpandItem("b_earlier_raw", datetime(2020, 1, 1, 14, 23, 15), sort_key=999),
        ]
        result = expand_unique_backward(items)
        assert result["a_later_raw"] == datetime(2020, 1, 1, 14, 23)
        assert result["b_earlier_raw"] == datetime(2020, 1, 1, 14, 22)

    def test_tiebreaker_dt_orders_same_day_cohort(self) -> None:
        """Within a same-day cohort (date-only `current`), `tiebreaker_dt`
        decides who anchors. Mirrors the real rekordbox case: StockDate is
        date-only, `created_at` is millisecond-precision and matches the UI
        order. The track with the later `created_at` should anchor at the
        snapped day; earlier ones get pushed back."""
        items = [
            ExpandItem(
                "earliest_create",
                date(2024, 6, 15),
                sort_key=999,
                tiebreaker_dt=datetime(2024, 6, 15, 8, 0, 0, 100_000),
            ),
            ExpandItem(
                "middle_create",
                date(2024, 6, 15),
                sort_key=1,
                tiebreaker_dt=datetime(2024, 6, 15, 8, 0, 0, 200_000),
            ),
            ExpandItem(
                "latest_create",
                date(2024, 6, 15),
                sort_key=500,
                tiebreaker_dt=datetime(2024, 6, 15, 8, 0, 0, 300_000),
            ),
        ]
        result = expand_unique_backward(items)
        assert result["latest_create"] == datetime(2024, 6, 15, 0, 0)
        assert result["middle_create"] == datetime(2024, 6, 14, 23, 59)
        assert result["earliest_create"] == datetime(2024, 6, 14, 23, 58)

    def test_tiebreaker_dt_takes_precedence_over_sort_key(self) -> None:
        """sort_key is the *last* fallback; tiebreaker_dt outranks it. A track
        with a low sort_key but later tiebreaker_dt must still anchor."""
        items = [
            ExpandItem(
                "low_sort_key_late_create",
                date(2024, 6, 15),
                sort_key=1,
                tiebreaker_dt=datetime(2024, 6, 15, 23, 59),
            ),
            ExpandItem(
                "high_sort_key_early_create",
                date(2024, 6, 15),
                sort_key=999,
                tiebreaker_dt=datetime(2024, 6, 15, 0, 1),
            ),
        ]
        result = expand_unique_backward(items)
        assert result["low_sort_key_late_create"] == datetime(2024, 6, 15, 0, 0)
        assert result["high_sort_key_early_create"] == datetime(2024, 6, 14, 23, 59)

    def test_sort_key_is_fallback_when_tiebreaker_dt_ties(self) -> None:
        """Identical tiebreaker_dt (e.g. batch-imported tracks all get the
        same created_at) — sort_key decides. Higher sort_key anchors."""
        same_ts = datetime(2024, 6, 15, 12, 0)
        items = [
            ExpandItem("a", date(2024, 6, 15), sort_key=10, tiebreaker_dt=same_ts),
            ExpandItem("b", date(2024, 6, 15), sort_key=20, tiebreaker_dt=same_ts),
            ExpandItem("c", date(2024, 6, 15), sort_key=30, tiebreaker_dt=same_ts),
        ]
        result = expand_unique_backward(items)
        assert result["c"] == datetime(2024, 6, 15, 0, 0)
        assert result["b"] == datetime(2024, 6, 14, 23, 59)
        assert result["a"] == datetime(2024, 6, 14, 23, 58)

    def test_tiebreaker_dt_default_none_preserves_legacy_behaviour(self) -> None:
        """Items constructed without tiebreaker_dt still sort by (current,
        sort_key) — the original behaviour. Documented contract for callers
        that don't have a high-resolution timestamp available."""
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=10),
            ExpandItem("b", date(2020, 1, 1), sort_key=20),
        ]
        result = expand_unique_backward(items)
        assert result["b"] == datetime(2020, 1, 1, 0, 0)
        assert result["a"] == datetime(2019, 12, 31, 23, 59)

    def test_plain_date_collisions_pack_backward_from_anchor(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=10),
            ExpandItem("b", date(2020, 1, 1), sort_key=20),
            ExpandItem("c", date(2020, 1, 1), sort_key=30),
        ]
        result = expand_unique_backward(items)
        assert result["c"] == datetime(2020, 1, 1, 0, 0)
        assert result["b"] == datetime(2019, 12, 31, 23, 59)
        assert result["a"] == datetime(2019, 12, 31, 23, 58)

    def test_collisions_only_push_within_their_own_cohort(self) -> None:
        items = [
            ExpandItem("anchor", datetime(2020, 1, 1, 14, 23), sort_key=1),
            ExpandItem("collider1", datetime(2020, 1, 1, 14, 23), sort_key=2),
            ExpandItem("later", datetime(2020, 1, 1, 14, 25), sort_key=3),
        ]
        result = expand_unique_backward(items)
        assert result["later"] == datetime(2020, 1, 1, 14, 25)
        assert result["collider1"] == datetime(2020, 1, 1, 14, 23)
        assert result["anchor"] == datetime(2020, 1, 1, 14, 22)

    def test_sort_key_breaks_ties_for_identical_timestamps(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=99),
            ExpandItem("b", date(2020, 1, 1), sort_key=1),
            ExpandItem("c", date(2020, 1, 1), sort_key=50),
        ]
        result = expand_unique_backward(items)
        assert result["b"] < result["c"] < result["a"]

    def test_output_is_strictly_monotonic_and_unique(self) -> None:
        items = [ExpandItem(f"id{i}", date(2020, 1, 1), sort_key=i) for i in range(500)]
        result = expand_unique_backward(items)
        ordered = sorted(result.items(), key=lambda kv: int(kv[0][2:]))
        for i in range(len(ordered) - 1):
            assert ordered[i][1] < ordered[i + 1][1]
        assert len(set(result.values())) == 500

    def test_idempotent_on_already_expanded_output(self) -> None:
        items = [ExpandItem(f"id{i}", date(2020, 1, 1), sort_key=i) for i in range(50)]
        first = expand_unique_backward(items)
        items2 = [ExpandItem(it.id, first[it.id], sort_key=it.sort_key) for it in items]
        second = expand_unique_backward(items2)
        assert first == second

    def test_cascades_freely_across_day_boundaries(self) -> None:
        items = [ExpandItem(f"id{i}", date(2020, 1, 1), sort_key=i) for i in range(2000)]
        result = expand_unique_backward(items)
        assert len(set(result.values())) == 2000
        days_used = {ts.date() for ts in result.values()}
        assert len(days_used) > 1

    def test_second_resolution_packs_more_tightly(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=10),
            ExpandItem("b", date(2020, 1, 1), sort_key=20),
            ExpandItem("c", date(2020, 1, 1), sort_key=30),
        ]
        result = expand_unique_backward(items, resolution=Resolution.SECOND)
        assert result["c"] == datetime(2020, 1, 1, 0, 0, 0)
        assert result["b"] == datetime(2019, 12, 31, 23, 59, 59)
        assert result["a"] == datetime(2019, 12, 31, 23, 59, 58)


class TestPackForSort:
    def test_empty_m3u_returns_empty(self) -> None:
        assert pack_for_sort([], []) == {}

    def test_single_m3u_item_keeps_its_snapped_date(self) -> None:
        result = pack_for_sort([ReorderItem("a", datetime(2024, 6, 15, 12, 30))], [])
        assert result == {"a": datetime(2024, 6, 15, 12, 30)}

    def test_anchors_on_last_m3u_entry(self) -> None:
        items = [
            ReorderItem("first", datetime(2020, 1, 1)),
            ReorderItem("middle", datetime(2022, 6, 15)),
            ReorderItem("anchor", datetime(2024, 12, 31, 14, 23)),
        ]
        result = pack_for_sort(items, [])
        assert result["anchor"] == datetime(2024, 12, 31, 14, 23)
        assert result["middle"] == datetime(2024, 12, 31, 14, 22)
        assert result["first"] == datetime(2024, 12, 31, 14, 21)

    def test_m3u_block_packs_at_resolution_step(self) -> None:
        items = [ReorderItem(f"id{i}", datetime(2024, 1, 1, 12, 0)) for i in range(5)]
        result = pack_for_sort(items, [])
        anchor = datetime(2024, 1, 1, 12, 0)
        assert result["id4"] == anchor
        assert result["id3"] == anchor - timedelta(minutes=1)
        assert result["id2"] == anchor - timedelta(minutes=2)
        assert result["id1"] == anchor - timedelta(minutes=3)
        assert result["id0"] == anchor - timedelta(minutes=4)

    def test_anchor_keeps_current_date_even_if_old(self) -> None:
        """User can put an old track at the end of m3u; the playlist relocates
        to that era. (Documented design choice.)"""
        items = [
            ReorderItem("recent", datetime(2024, 12, 31)),
            ReorderItem("old_anchor", datetime(2020, 1, 1, 8, 0)),
        ]
        result = pack_for_sort(items, [])
        assert result["old_anchor"] == datetime(2020, 1, 1, 8, 0)
        assert result["recent"] == datetime(2020, 1, 1, 7, 59)

    def test_non_m3u_outside_window_is_not_disturbed(self) -> None:
        m3u = [
            ReorderItem("m_a", datetime(2024, 6, 15, 12, 0)),
            ReorderItem("m_b", datetime(2024, 6, 15, 12, 5)),
        ]
        others = [
            ExpandItem("o_far_past", datetime(2020, 1, 1), sort_key=1),
            ExpandItem("o_far_future", datetime(2025, 1, 1), sort_key=2),
        ]
        result = pack_for_sort(m3u, others)
        assert "o_far_past" not in result
        assert "o_far_future" not in result
        assert result["m_b"] == datetime(2024, 6, 15, 12, 5)
        assert result["m_a"] == datetime(2024, 6, 15, 12, 4)

    def test_non_m3u_collision_cascades_backward(self) -> None:
        m3u = [
            ReorderItem("m_a", datetime(2024, 6, 15, 12, 0)),
            ReorderItem("m_b", datetime(2024, 6, 15, 12, 5)),
        ]
        others = [
            ExpandItem("collider", datetime(2024, 6, 15, 12, 5), sort_key=1),
        ]
        result = pack_for_sort(m3u, others)
        assert result["m_b"] == datetime(2024, 6, 15, 12, 5)
        assert result["m_a"] == datetime(2024, 6, 15, 12, 4)
        assert result["collider"] == datetime(2024, 6, 15, 12, 3)

    def test_cascade_preserves_relative_order_among_cascaded_items(self) -> None:
        """Two non-m3u items both inside the m3u window — both must cascade,
        and the one originally later in time must land at a later final slot."""
        m3u = [ReorderItem(f"m_{i}", datetime(2024, 6, 15, 12, 4)) for i in range(5)]
        others = [
            ExpandItem("later", datetime(2024, 6, 15, 12, 3), sort_key=1),
            ExpandItem("earlier", datetime(2024, 6, 15, 12, 1), sort_key=2),
        ]
        result = pack_for_sort(m3u, others)
        assert result["earlier"] < result["later"]

    def test_cascade_uses_tiebreaker_dt_for_same_day_non_m3u_items(self) -> None:
        """When two cascaded non-m3u items share a date-only `current`, their
        relative order in the cascade must follow `tiebreaker_dt` (the rekordbox
        UI order), not `sort_key`."""
        m3u = [ReorderItem(f"m_{i}", date(2024, 6, 15)) for i in range(3)]
        others = [
            ExpandItem(
                "low_sort_late_create",
                date(2024, 6, 15),
                sort_key=1,
                tiebreaker_dt=datetime(2024, 6, 15, 23, 59),
            ),
            ExpandItem(
                "high_sort_early_create",
                date(2024, 6, 15),
                sort_key=999,
                tiebreaker_dt=datetime(2024, 6, 15, 0, 1),
            ),
        ]
        result = pack_for_sort(m3u, others)
        assert result["low_sort_late_create"] > result["high_sort_early_create"]

    def test_id_in_both_m3u_and_others_uses_m3u_assignment(self) -> None:
        m3u = [
            ReorderItem("a", datetime(2024, 1, 1, 10, 0)),
            ReorderItem("b", datetime(2024, 1, 1, 10, 5)),
        ]
        others = [
            ExpandItem("a", datetime(2020, 1, 1), sort_key=1),
            ExpandItem("c", datetime(2024, 1, 1, 10, 10), sort_key=2),
        ]
        result = pack_for_sort(m3u, others)
        assert result["a"] == datetime(2024, 1, 1, 10, 4)
        assert result["b"] == datetime(2024, 1, 1, 10, 5)
        assert "c" not in result

    def test_idempotent_when_already_packed(self) -> None:
        m3u = [
            ReorderItem("a", datetime(2024, 6, 15, 12, 3)),
            ReorderItem("b", datetime(2024, 6, 15, 12, 4)),
            ReorderItem("c", datetime(2024, 6, 15, 12, 5)),
        ]
        others = [ExpandItem("o1", datetime(2023, 1, 1), sort_key=1)]
        first = pack_for_sort(m3u, others)
        m3u_after = [ReorderItem(it.id, first[it.id]) for it in m3u]
        second = pack_for_sort(m3u_after, others)
        assert first == second

    def test_minute_resolution_snaps_anchor(self) -> None:
        result = pack_for_sort(
            [ReorderItem("a", datetime(2024, 1, 1, 12, 0, 45))],
            [],
        )
        assert result["a"] == datetime(2024, 1, 1, 12, 0, 0)

    def test_second_resolution_packs_at_one_second(self) -> None:
        items = [
            ReorderItem("a", datetime(2024, 1, 1, 12, 0, 0)),
            ReorderItem("b", datetime(2024, 1, 1, 12, 0, 5)),
        ]
        result = pack_for_sort(items, [], resolution=Resolution.SECOND)
        assert result["b"] == datetime(2024, 1, 1, 12, 0, 5)
        assert result["a"] == datetime(2024, 1, 1, 12, 0, 4)

    def test_all_assigned_dates_are_globally_unique(self) -> None:
        m3u = [ReorderItem(f"m{i}", datetime(2024, 6, 15, 12, 0)) for i in range(20)]
        base = datetime(2024, 6, 15, 11, 0)
        others = [ExpandItem(f"o{i}", base + timedelta(minutes=i), sort_key=i) for i in range(50)]
        result = pack_for_sort(m3u, others)
        final_dates: list[datetime] = list(result.values())
        for it in others:
            if it.id not in result:
                final_dates.append(base + timedelta(minutes=it.sort_key))
        assert len(set(final_dates)) == len(final_dates)

    def test_anchor_with_only_date_no_time_uses_midnight(self) -> None:
        result = pack_for_sort(
            [
                ReorderItem("first", date(2024, 1, 1)),
                ReorderItem("anchor", date(2024, 6, 15)),
            ],
            [],
        )
        assert result["anchor"] == datetime(2024, 6, 15, 0, 0)
        assert result["first"] == datetime(2024, 6, 14, 23, 59)


class TestDayResolution:
    """``Resolution.DAY`` is the iOS-honored option for stockdate sort: each
    cascade step is exactly one day, so two same-day collisions produce
    different YYYY-MM-DD strings and iOS doesn't fall back to artist sort."""

    def test_expand_cascades_at_one_day_step(self) -> None:
        items = [
            ExpandItem("a", date(2024, 6, 15), sort_key=10),
            ExpandItem("b", date(2024, 6, 15), sort_key=20),
            ExpandItem("c", date(2024, 6, 15), sort_key=30),
        ]
        result = expand_unique_backward(items, resolution=Resolution.DAY)
        assert result["c"] == datetime(2024, 6, 15, 0, 0)
        assert result["b"] == datetime(2024, 6, 14, 0, 0)
        assert result["a"] == datetime(2024, 6, 13, 0, 0)

    def test_pack_for_sort_cascades_non_m3u_at_day_step(self) -> None:
        m3u = [
            ReorderItem("m_a", datetime(2024, 6, 15)),
            ReorderItem("m_b", datetime(2024, 6, 16)),
        ]
        others = [
            ExpandItem("collider", datetime(2024, 6, 16), sort_key=1),
        ]
        result = pack_for_sort(m3u, others, resolution=Resolution.DAY)
        assert result["m_b"] == datetime(2024, 6, 16, 0, 0)
        assert result["m_a"] == datetime(2024, 6, 15, 0, 0)
        assert result["collider"] == datetime(2024, 6, 14, 0, 0)

    def test_day_resolution_anchor_snaps_to_midnight(self) -> None:
        result = pack_for_sort(
            [ReorderItem("a", datetime(2024, 6, 15, 14, 23, 45))],
            [],
            resolution=Resolution.DAY,
        )
        assert result["a"] == datetime(2024, 6, 15, 0, 0)

    def test_day_resolution_consecutive_collisions_cross_month(self) -> None:
        items = [ExpandItem(f"id{i}", date(2024, 3, 1), sort_key=i) for i in range(40)]
        result = expand_unique_backward(items, resolution=Resolution.DAY)
        assert len(set(result.values())) == 40
        assert result["id39"] == datetime(2024, 3, 1, 0, 0)
        assert result["id0"] == datetime(2024, 3, 1, 0, 0) - timedelta(days=39)


class TestIntSpace:
    """``IntSpace`` powers --target year. Convention: lower value = newer.
    M3u anchor fixed at min_value (0). Overflow past max_value (9999) all
    share the cap output."""

    def test_anchor_fixed_at_min_value_regardless_of_m3u_current(self) -> None:
        """IntSpace ignores the last m3u item's current value; anchor is fixed
        at min_value so the newest m3u track always lands at year 0."""
        space = IntSpace()
        m3u = [
            ReorderItem("first", 5000),
            ReorderItem("middle", 1234),
            ReorderItem("last", 9999),
        ]
        result = pack_for_sort_field(m3u, [], space)
        assert result["last"] == 0
        assert result["middle"] == 1
        assert result["first"] == 2

    def test_non_m3u_outside_window_is_not_disturbed(self) -> None:
        """Real ReleaseYear values like 1985, 2024 don't collide with the
        small m3u block, so they keep their existing year unchanged."""
        space = IntSpace()
        m3u = [ReorderItem(f"m{i}", 0) for i in range(50)]
        others = [
            ExpandItem("vintage", 1985, sort_key=1),
            ExpandItem("recent", 2024, sort_key=2),
        ]
        result = pack_for_sort_field(m3u, others, space)
        assert "vintage" not in result
        assert "recent" not in result

    def test_non_m3u_collision_cascades_older(self) -> None:
        """A non-m3u track inside the m3u year window cascades older (higher
        year) until it finds a free slot."""
        space = IntSpace()
        m3u = [ReorderItem(f"m{i}", 0) for i in range(5)]
        others = [
            ExpandItem("collider", 2, sort_key=1),
        ]
        result = pack_for_sort_field(m3u, others, space)
        assert result["collider"] == 5

    def test_overflow_clamps_at_max_value(self) -> None:
        """M3u block beyond the cap stacks at max_value (overflow share-bucket)."""
        space = IntSpace(min_value=0, max_value=5)
        m3u = [ReorderItem(f"m{i}", 0) for i in range(10)]
        result = pack_for_sort_field(m3u, [], space)
        assert result["m9"] == 0
        assert result["m8"] == 1
        assert result["m4"] == 5
        assert result["m3"] == 5
        assert result["m2"] == 5
        assert result["m1"] == 5
        assert result["m0"] == 5

    def test_overflow_at_9999_for_huge_m3u(self) -> None:
        """The user's 'colliding at 9999' guarantee for >10K m3u tracks."""
        space = IntSpace(min_value=0, max_value=9999)
        m3u = [ReorderItem(f"m{i}", 0) for i in range(10_500)]
        result = pack_for_sort_field(m3u, [], space)
        assert result["m10499"] == 0
        assert result["m9500"] == 999
        assert result["m499"] == 9999
        assert result["m0"] == 9999

    def test_non_m3u_with_year_above_cap_clamps_to_cap(self) -> None:
        """Non-m3u track with ReleaseYear above cap snaps to cap (no cascade)."""
        space = IntSpace(min_value=0, max_value=9999)
        m3u = [ReorderItem("m", 0)]
        others = [ExpandItem("future", 12345, sort_key=1)]
        result = pack_for_sort_field(m3u, others, space)
        assert result["m"] == 0
        assert result.get("future", 12345) == 12345 or result.get("future") == 9999

    def test_expand_unique_backward_field_preserves_year_order(self) -> None:
        """Generic expand for years: items keep their existing year unless
        a strictly older item already claimed the same slot."""
        space = IntSpace()
        items = [
            ExpandItem("a", 1985, sort_key=1),
            ExpandItem("b", 1985, sort_key=2),
            ExpandItem("c", 2024, sort_key=3),
        ]
        result = expand_unique_backward_field(items, space)
        assert result["c"] == 2024
        assert result["b"] == 1985
        assert result["a"] == 1986

    def test_pack_for_sort_field_with_int_is_idempotent(self) -> None:
        """Same inputs (m3u updated to its assigned years; non-m3u currents
        unchanged) must produce the same output on a re-run."""
        space = IntSpace()
        m3u = [ReorderItem(f"m{i}", 0) for i in range(20)]
        others = [
            ExpandItem("o1", 1985, sort_key=1),
            ExpandItem("o2", 5, sort_key=2),
        ]
        first = pack_for_sort_field(m3u, others, space)
        m3u_after = [ReorderItem(it.id, first[it.id]) for it in m3u]
        second = pack_for_sort_field(m3u_after, others, space)
        assert first == second


class TestPaddedIntSpace:
    """``PaddedIntSpace`` powers --target genre/album. Lexicographic ordering
    of zero-padded strings matches numeric ordering."""

    def test_emits_zero_padded_strings_at_fixed_width(self) -> None:
        space = PaddedIntSpace(width=4)
        m3u = [ReorderItem(f"m{i}", 0) for i in range(5)]
        result = pack_for_sort_field(m3u, [], space)
        assert result["m4"] == "0000"
        assert result["m3"] == "0001"
        assert result["m2"] == "0002"
        assert result["m1"] == "0003"
        assert result["m0"] == "0004"

    def test_lexicographic_order_matches_numeric_for_padded(self) -> None:
        space = PaddedIntSpace(width=3)
        m3u = [ReorderItem(f"m{i}", 0) for i in range(50)]
        result = pack_for_sort_field(m3u, [], space)
        ordered = sorted(result.values())
        numeric = sorted(int(v) for v in result.values())
        assert [int(v) for v in ordered] == numeric

    def test_non_numeric_genre_names_are_excluded_by_caller(self) -> None:
        """PaddedIntSpace.snap_axis only accepts ints / numeric strings.
        Caller is expected to pre-filter; this test just documents that real
        non-numeric names raise so the caller knows to filter."""
        space = PaddedIntSpace(width=4)
        try:
            space.snap_axis("Acid House")
        except (TypeError, ValueError):
            return
        msg = "expected TypeError / ValueError on non-numeric"
        raise AssertionError(msg)

    def test_numeric_string_collision_cascades_at_padding(self) -> None:
        """If a non-m3u track happens to have a numeric Genre name in our
        namespace (e.g. from a prior shuffle run), it cascades older."""
        space = PaddedIntSpace(width=2)
        m3u = [ReorderItem(f"m{i}", 0) for i in range(5)]
        others = [ExpandItem("from_prior_shuffle", "02", sort_key=1)]
        result = pack_for_sort_field(m3u, others, space)
        assert result["from_prior_shuffle"] == "05"

    def test_default_max_value_matches_width_capacity(self) -> None:
        """width=3 implies max_value=999 (10**3 - 1) by default."""
        space = PaddedIntSpace(width=3)
        assert space.max_value == 999


class TestExpandToFieldPattern:
    """The ``expand-dates --target year/genre/album`` flow is a thin call to
    ``pack_for_sort_field`` with the entire StockDate-sorted library as the
    m3u block. These tests pin the pattern so changes to either side stay
    compatible with the expand command's expectations."""

    def test_full_library_as_m3u_assigns_sequential_years(self) -> None:
        """With every alive track passed in StockDate order (oldest first),
        the newest gets year 0 and each older track gets year+1."""
        space = IntSpace(min_value=0, max_value=9999)
        items_oldest_first = [ReorderItem(f"t{i}", current=0) for i in range(10)]
        result = pack_for_sort_field(items_oldest_first, [], space)
        assert result["t9"] == 0
        assert result["t8"] == 1
        assert result["t0"] == 9

    def test_full_library_overflow_at_9999_for_huge_library(self) -> None:
        """Library beyond the 0..9999 capacity collapses oldest tracks at 9999."""
        space = IntSpace(min_value=0, max_value=9999)
        items = [ReorderItem(f"t{i}", current=0) for i in range(10_500)]
        result = pack_for_sort_field(items, [], space)
        assert result["t10499"] == 0
        assert result["t499"] == 9999
        assert result["t0"] == 9999

    def test_full_library_as_m3u_assigns_padded_genre_names(self) -> None:
        """Genre/album expand: zero-padded numerics, lexicographic sort
        matches numeric sort."""
        space = PaddedIntSpace(width=3)
        items = [ReorderItem(f"t{i}", current=0) for i in range(5)]
        result = pack_for_sort_field(items, [], space)
        assert result["t4"] == "000"
        assert result["t3"] == "001"
        assert result["t0"] == "004"
        assert sorted(result.values()) == ["000", "001", "002", "003", "004"]
