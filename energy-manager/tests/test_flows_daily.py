"""Tests for the daily household flows summary (long-term reporting)."""

import pandas as pd

from src.flows_daily import compute_flows

HT, NT, FEED = 0.3202, 0.2434, 0.09


def _hourly(values, start="2026-07-04 06:00"):
    idx = pd.date_range(start, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx)


def test_consumers_and_balance():
    f = compute_flows(
        daily_kwh={"car": 13.7, "desk": 10.0, "bench": 0.5, "house": 13.5,
                   "import": 5.0, "export": 40.0},
        hourly_import_kwh=pd.Series(dtype=float),
        expensive_mask=pd.Series(dtype=bool),
        production_kwh=62.0,
        ht_chf_kwh=HT, nt_chf_kwh=NT, feed_in_chf_kwh=FEED,
    )
    assert f["car_kwh"] == 13.7
    assert f["lab_kwh"] == 10.5
    assert f["house_rest_kwh"] == 3.0
    assert f["consumption_kwh"] == 62.0 - 40.0 + 5.0
    assert f["autarky"] == round(1 - 5.0 / 27.0, 3)
    assert f["self_consumption"] == round(22.0 / 62.0, 3)
    assert f["export_revenue_chf"] == round(40.0 * FEED, 3)


def test_tariff_attribution():
    # 2 kWh in an HT hour, 3 kWh in an NT hour
    imp = _hourly([2.0, 3.0])
    mask = pd.Series([True, False], index=imp.index)
    f = compute_flows(
        daily_kwh={"import": 5.0, "export": 0.0, "house": 5.0},
        hourly_import_kwh=imp, expensive_mask=mask,
        production_kwh=0.0,
        ht_chf_kwh=HT, nt_chf_kwh=NT, feed_in_chf_kwh=FEED,
    )
    assert f["import_cost_chf"] == round(2 * HT + 3 * NT, 3)
    assert f["net_cost_chf"] == f["import_cost_chf"]


def test_no_hourly_data_falls_back_to_nt():
    f = compute_flows(
        daily_kwh={"import": 10.0},
        hourly_import_kwh=pd.Series(dtype=float),
        expensive_mask=pd.Series(dtype=bool),
        production_kwh=0.0,
        ht_chf_kwh=HT, nt_chf_kwh=NT, feed_in_chf_kwh=FEED,
    )
    assert f["import_cost_chf"] == round(10.0 * NT, 3)
    assert "autarky" not in f or f["autarky"] == 0.0
