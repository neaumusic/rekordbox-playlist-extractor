"""Tests for the timestamp redistribution algorithms."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from rbx.dates import (
    ExpandItem,
    ReorderItem,
    Resolution,
    expand_within_day,
    redistribute_range,
)


class TestExpandWithinDay:
    def test_single_item_per_day_is_passthrough(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=1),
            ExpandItem("b", date(2020, 1, 2), sort_key=1),
        ]
        result = expand_within_day(items)
        assert result["a"] == datetime(2020, 1, 1, 0, 0)
        assert result["b"] == datetime(2020, 1, 2, 0, 0)

    def test_three_items_same_day_get_distinct_minutes(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=10),
            ExpandItem("b", date(2020, 1, 1), sort_key=20),
            ExpandItem("c", date(2020, 1, 1), sort_key=30),
        ]
        result = expand_within_day(items)
        assert result["a"] == datetime(2020, 1, 1, 0, 0)
        assert result["b"] == datetime(2020, 1, 1, 0, 1)
        assert result["c"] == datetime(2020, 1, 1, 0, 2)

    def test_sort_key_determines_order_within_day(self) -> None:
        items = [
            ExpandItem("a", date(2020, 1, 1), sort_key=99),
            ExpandItem("b", date(2020, 1, 1), sort_key=1),
            ExpandItem("c", date(2020, 1, 1), sort_key=50),
        ]
        result = expand_within_day(items)
        assert result["b"] < result["c"] < result["a"]

    def test_idempotent_when_already_unique(self) -> None:
        items = [
            ExpandItem("a", datetime(2020, 1, 1, 10, 0), sort_key=1),
            ExpandItem("b", datetime(2020, 1, 1, 14, 30), sort_key=2),
        ]
        result = expand_within_day(items)
        assert result["a"] == datetime(2020, 1, 1, 10, 0)
        assert result["b"] == datetime(2020, 1, 1, 14, 30)

    def test_re_assigns_when_not_unique_at_minute_precision(self) -> None:
        items = [
            ExpandItem("a", datetime(2020, 1, 1, 10, 0, 5), sort_key=1),
            ExpandItem("b", datetime(2020, 1, 1, 10, 0, 30), sort_key=2),
        ]
        result = expand_within_day(items)
        assert result["a"] == datetime(2020, 1, 1, 0, 0)
        assert result["b"] == datetime(2020, 1, 1, 0, 1)

    def test_falls_back_to_seconds_when_day_is_full(self) -> None:
        items = [ExpandItem(f"id{i}", date(2020, 1, 1), sort_key=i) for i in range(1500)]
        result = expand_within_day(items)
        assert len(set(result.values())) == 1500
        assert all(t.date() == date(2020, 1, 1) for t in result.values())

    def test_raises_when_day_exceeds_second_capacity(self) -> None:
        items = [ExpandItem(f"id{i}", date(2020, 1, 1), sort_key=i) for i in range(86401)]
        with pytest.raises(ValueError):
            expand_within_day(items)


class TestRedistributeRange:
    def test_single_item_passthrough(self) -> None:
        items = [ReorderItem("a", datetime(2020, 1, 1, 12, 30))]
        result = redistribute_range(items)
        assert result["a"] == datetime(2020, 1, 1, 12, 30)

    def test_preserves_lo_hi_anchors_when_range_is_wide_enough(self) -> None:
        items = [
            ReorderItem("a", datetime(2020, 1, 1)),
            ReorderItem("b", datetime(2024, 1, 1)),
            ReorderItem("c", datetime(2022, 1, 1)),
        ]
        result = redistribute_range(items)
        assert result["a"] == datetime(2020, 1, 1)
        assert result["c"] == datetime(2024, 1, 1)
        assert datetime(2020, 1, 1) < result["b"] < datetime(2024, 1, 1)

    def test_expands_range_when_too_narrow(self) -> None:
        items = [
            ReorderItem(f"id{i}", datetime(2020, 1, 1, 12, 0)) for i in range(5)
        ]
        result = redistribute_range(items)
        ordered = [result[f"id{i}"] for i in range(5)]
        assert all(ordered[i + 1] > ordered[i] for i in range(4))
        for i in range(4):
            assert ordered[i + 1] - ordered[i] == timedelta(minutes=1)

    def test_strictly_monotonic_and_unique(self) -> None:
        items = [
            ReorderItem(f"id{i}", datetime(2020, 1, 1) + timedelta(days=i))
            for i in range(20)
        ]
        result = redistribute_range(items)
        ordered = [result[f"id{i}"] for i in range(20)]
        for i in range(19):
            assert ordered[i + 1] > ordered[i]
        assert len(set(ordered)) == 20

    def test_minute_resolution_snapping(self) -> None:
        items = [
            ReorderItem("a", datetime(2020, 1, 1, 0, 0, 30)),
            ReorderItem("b", datetime(2020, 1, 1, 1, 0, 45)),
        ]
        result = redistribute_range(items)
        for ts in result.values():
            assert ts.second == 0
            assert ts.microsecond == 0

    def test_second_resolution(self) -> None:
        items = [
            ReorderItem("a", datetime(2020, 1, 1, 12, 0, 0)),
            ReorderItem("b", datetime(2020, 1, 1, 12, 0, 30)),
            ReorderItem("c", datetime(2020, 1, 1, 12, 1, 0)),
        ]
        result = redistribute_range(items, resolution=Resolution.SECOND)
        for ts in result.values():
            assert ts.microsecond == 0
        assert result["a"] < result["b"] < result["c"]

    def test_empty_input_returns_empty(self) -> None:
        assert redistribute_range([]) == {}
