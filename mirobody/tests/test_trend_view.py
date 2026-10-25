"""A trend is a computation, not an impression.

`stats` reports `first`/`last`, which are single readings: on a sleep series that went
6.18 -> 7.2 a 9 % decline between its thirds read as a rise, and every model asked to
classify that series answered "no trend". These tests pin the arithmetic and the vocabulary
that `view="trend"` adds, on shapes whose answer is known.
"""
from __future__ import annotations

import math

from mirobody.agent.tools.health_indicators_service import _trend_rows


def series(values: list[float], indicator: str = "x") -> list[dict]:
    return [{"indicator": indicator, "period": f"2025-01-{i + 1:02d}", "avg": value, "unit": "u"}
            for i, value in enumerate(values)]


def test_a_rise_is_up_with_a_positive_slope() -> None:
    row = _trend_rows(series([1 + 0.3 * i for i in range(30)]))[0]
    assert row["direction"] == "up"
    assert row["delta_pct"] > 100
    assert row["slope_per_day"] > 0


def test_a_fall_is_down() -> None:
    row = _trend_rows(series([10 - 0.3 * i for i in range(30)]))[0]
    assert row["direction"] == "down"
    assert row["delta_pct"] < -100


def test_a_series_that_only_jitters_is_flat() -> None:
    row = _trend_rows(series([7.0 + (0.05 if i % 2 else -0.05) for i in range(30)]))[0]
    assert row["direction"] == "flat"
    assert abs(row["delta_pct"]) < 2


def test_a_single_hump_is_named_not_called_flat() -> None:
    row = _trend_rows(series([7.0 + 1.5 * math.sin(math.pi * i / 29) for i in range(30)]))[0]
    assert row["shape"] == "hump"


def test_a_single_dip_is_named() -> None:
    row = _trend_rows(series([7.0 - 2.0 * math.sin(math.pi * i / 29) for i in range(30)]))[0]
    assert row["shape"] == "dip"


def test_a_step_down_reads_as_down() -> None:
    row = _trend_rows(series([8.0] * 15 + [6.0] * 15))[0]
    assert row["direction"] == "down"
    assert row["shape"] == "down"


def test_first_and_last_alone_would_have_missed_the_decline() -> None:
    """The series that started this: falling thirds, rising ends."""
    values = [6.183, 7.883, 6.183, 8.05, 7.967, 7.717, 7.433, 8.133, 8.833, 6.733,
              4.65, 8.2, 7.35, 6.983, 8.717, 6.833, 7.983, 8.367, 8.65, 6.9,
              7.817, 7.467, 6.083, 4.817, 8.5, 8.217, 5.667, 7.2]
    row = _trend_rows(series(values))[0]
    assert values[-1] > values[0]           # ends say "up"
    assert row["direction"] == "down"       # thirds say "down", and that is the trend
    assert row["delta_pct"] < 0


def test_too_few_points_says_so() -> None:
    row = _trend_rows(series([1.0, 2.0]))[0]
    assert row["direction"] == "insufficient"
