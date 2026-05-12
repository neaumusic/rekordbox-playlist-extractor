"""Tests for the timestamp redistribution algorithms."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from rbx.dates import (
    ExpandItem,
    ReorderItem,
    Resolution,
    expand_unique_backward,
    pack_for_sort,
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
        m3u = [
            ReorderItem(f"m_{i}", datetime(2024, 6, 15, 12, 4)) for i in range(5)
        ]
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
        m3u = [
            ReorderItem(f"m_{i}", date(2024, 6, 15)) for i in range(3)
        ]
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
        others = [
            ExpandItem(f"o{i}", base + timedelta(minutes=i), sort_key=i)
            for i in range(50)
        ]
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
