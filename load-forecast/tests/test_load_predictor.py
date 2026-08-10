"""Tests for the load-forecast time-of-day profile (FSD Section 4).

`build_profile` and `generate_forecast` are free of I/O — they take and return
DataFrames — so the algorithm is tested directly. Only `load_historical_data`
touches InfluxDB.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.load_predictor import LoadPredictor

TZ = "Europe/Zurich"


@pytest.fixture
def predictor():
    return LoadPredictor(host="localhost", port=8087, token="t", org="o", local_timezone=TZ)


def history(values_by_local_time: dict[str, list[float]]) -> pd.DataFrame:
    """Build a history frame from {"HH:MM": [values...]} in local time.

    Each value is placed on a separate day so it lands in the same slot.
    """
    times, powers = [], []
    for hhmm, values in values_by_local_time.items():
        hour, minute = (int(x) for x in hhmm.split(":"))
        for day, value in enumerate(values, start=1):
            times.append(pd.Timestamp(2026, 3, day, hour, minute, tz=TZ))
            powers.append(value)
    return pd.DataFrame({"load_power": powers}, index=pd.DatetimeIndex(times, name="time"))


class TestSlotMapping:
    """96 slots of 15 min; slot = hour*4 + minute//15 (FSD Section 4)."""

    @pytest.mark.parametrize(
        ("hhmm", "expected_slot"),
        [
            ("00:00", 0),
            ("00:14", 0),   # boundary: still slot 0
            ("00:15", 1),   # boundary: first minute of slot 1
            ("12:00", 48),
            ("23:45", 95),
            ("23:59", 95),
        ],
    )
    def test_slot_boundaries(self, predictor, hhmm, expected_slot) -> None:
        profile = predictor.build_profile(history({hhmm: [500.0]}))
        assert list(profile.index) == [expected_slot]

    def test_all_96_slots(self, predictor) -> None:
        full_day = {f"{h:02d}:{m:02d}": [100.0] for h in range(24) for m in (0, 15, 30, 45)}
        profile = predictor.build_profile(history(full_day))
        assert len(profile) == 96
        assert profile.index.min() == 0
        assert profile.index.max() == 95


class TestPercentiles:
    """P10 / P50 / P90 per slot (FSD Section 4)."""

    def test_known_distribution(self, predictor) -> None:
        # 0..100 in steps of 10 → p10=10, p50=50, p90=90 (linear interpolation).
        profile = predictor.build_profile(history({"08:00": [float(v) for v in range(0, 101, 10)]}))
        row = profile.loc[32]  # 08:00 → slot 32
        assert row["p10"] == pytest.approx(10.0)
        assert row["p50"] == pytest.approx(50.0)
        assert row["p90"] == pytest.approx(90.0)
        assert row["count"] == 11

    def test_ordering_holds(self, predictor) -> None:
        profile = predictor.build_profile(
            history({"06:00": [100.0, 200.0, 900.0], "18:00": [50.0, 60.0, 70.0]})
        )
        for slot in profile.index:
            row = profile.loc[slot]
            assert row["p10"] <= row["p50"] <= row["p90"]

    def test_slots_are_independent(self, predictor) -> None:
        """A spike in one slot must not move another."""
        profile = predictor.build_profile(
            history({"07:00": [1000.0, 1000.0], "07:15": [100.0, 100.0]})
        )
        assert profile.loc[28]["p50"] == pytest.approx(1000.0)
        assert profile.loc[29]["p50"] == pytest.approx(100.0)


class TestForecastShape:
    """Horizon length and start alignment (FSD Section 4)."""

    @pytest.fixture
    def built(self, predictor):
        full_day = {f"{h:02d}:{m:02d}": [200.0] for h in range(24) for m in (0, 15, 30, 45)}
        predictor.build_profile(history(full_day))
        return predictor

    def test_horizon_length(self, built) -> None:
        assert len(built.generate_forecast(hours=120)) == 480
        assert len(built.generate_forecast(hours=48)) == 192

    def test_start_aligned_down_to_quarter(self, built) -> None:
        forecast = built.generate_forecast(
            start_time=datetime(2026, 8, 10, 9, 7, 43, tzinfo=UTC), hours=1
        )
        assert forecast.index[0] == pd.Timestamp("2026-08-10T09:00:00Z")

    def test_index_is_utc_and_quarter_hourly(self, built) -> None:
        forecast = built.generate_forecast(
            start_time=datetime(2026, 8, 10, 9, 0, tzinfo=UTC), hours=1
        )
        assert str(forecast.index.tz) == "UTC"
        assert list(forecast.index.diff()[1:]) == [pd.Timedelta("15min")] * 3


class TestLocalTimeSeam:
    """Profile slots are local; the forecast index is UTC (FSD Section 4)."""

    def test_utc_timestamp_reads_the_local_slot(self, predictor) -> None:
        # Only 08:00 local is populated. In August (CEST, UTC+2) that is 06:00 UTC.
        predictor.build_profile(history({"08:00": [777.0], "08:15": [111.0]}))
        forecast = predictor.generate_forecast(
            start_time=datetime(2026, 8, 10, 6, 0, tzinfo=UTC), hours=1
        )
        assert forecast.iloc[0]["power_w_p50"] == pytest.approx(777.0)
        assert forecast.iloc[1]["power_w_p50"] == pytest.approx(111.0)


class TestSparseHistory:
    """A slot with no history falls back to the median across slots."""

    def test_missing_slot_uses_median_fallback(self, predictor) -> None:
        predictor.build_profile(history({"00:00": [100.0], "12:00": [300.0]}))
        forecast = predictor.generate_forecast(
            start_time=datetime(2026, 8, 10, 4, 0, tzinfo=UTC), hours=1
        )
        # 06:00 local is an unpopulated slot → median of {100, 300}.
        assert forecast.iloc[0]["power_w_p50"] == pytest.approx(200.0)
        assert not forecast.isna().any().any()


class TestEmptyHistoryIsRefused:
    """An empty history must fail loudly, never yield a NaN forecast.

    Regression: `load_historical_data` tested `result.empty` before `dropna()`,
    so a window of all-null values reached `build_profile` empty, produced an
    empty profile, and every forecast point fell through to
    `profile["p10"].median()` — NaN on an empty frame. A full-length forecast of
    NaNs was then written to InfluxDB and consumed by EnergyManager.
    """

    def test_build_profile_refuses_empty(self, predictor) -> None:
        empty = pd.DataFrame(
            {"load_power": []}, index=pd.DatetimeIndex([], tz=TZ, name="time")
        )
        with pytest.raises(ValueError, match="empty history"):
            predictor.build_profile(empty)

    def test_generate_forecast_refuses_empty_profile(self, predictor) -> None:
        predictor.profile = pd.DataFrame(columns=["p10", "p50", "p90", "mean", "count"])
        with pytest.raises(ValueError, match="empty"):
            predictor.generate_forecast(hours=1)

    def test_generate_forecast_refuses_unbuilt_profile(self, predictor) -> None:
        with pytest.raises(ValueError, match="Profile not built"):
            predictor.generate_forecast(hours=1)


class TestSourceEntityDefault:
    """The source entity was renamed 2026-02-27; the old name returns no data."""

    def test_default_is_the_current_entity(self) -> None:
        assert LoadPredictor(host="h", port=1, token="t", org="o").load_entity == (
            "house_load_power"
        )
