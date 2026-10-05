"""Regressions for account identity, calendar gaps and causal inference."""
import pandas as pd
import pytest

from ml.dataset import build_features, regression_metrics, temporal_split
from ml.predict import compute_lags


def invoice(account, month, consumption, number=None, sede="A", year=2025):
    return {"Oficina": sede, "Cuenta": account, "N° Factura": number or f"{account}-{year}-{month}",
            "Año": year, "Mes": month, "kWh": consumption}


def test_two_accounts_in_one_office_keep_separate_targets():
    rows = [invoice(a, m, base + m) for a, base in [("001", 100), ("002", 900)] for m in range(1, 5)]
    f = build_features(rows)
    march = f[f.Mes.eq(3)].set_index("Cuenta")
    assert march.loc["001", "target_consumo"] == 103
    assert march.loc["002", "target_consumo"] == 903
    assert march.loc["001", "lag_1"] == 102


def test_missing_month_is_not_previous_month_or_training_label():
    rows = [invoice("001", m, v) for m, v in [(1, 10), (2, 20), (4, 40), (5, 50)]]
    f = build_features(rows)
    assert 4 not in f.Mes.tolist()  # March unknown; strict policy abstains for April.
    may = f[f.Mes.eq(5)].iloc[0]
    assert may.target_consumo == 50
    assert may.lag_1 == 40
    assert may.rolling_3 == 30  # February and April, never a fabricated March.


def test_duplicate_invoice_across_offices_is_not_an_extra_consumption():
    rows = [invoice("001", m, 10*m) for m in range(1, 5)]
    rows.append(invoice("001", 3, 30, sede="OTHER"))
    f = build_features(rows)
    assert len(f[f.Mes.eq(3)]) == 1
    assert f[f.Mes.eq(4)].iloc[0].lag_1 == 30


def test_distinct_invoice_numbers_same_period_are_quarantined():
    rows = [invoice("001", m, 10*m) for m in range(1, 5)]
    rows.append(invoice("001", 2, 20, number="replacement"))
    f = build_features(rows)
    assert 2 not in f.Mes.tolist()
    assert 3 not in f.Mes.tolist()


def test_zero_observed_consumption_remains_a_label():
    f = build_features([invoice("001", m, v) for m, v in [(1, 10), (2, 0), (3, 30)]])
    feb = f[f.Mes.eq(2)].iloc[0]
    assert feb.target_consumo == 0
    assert f[f.Mes.eq(3)].iloc[0].lag_1 == 0


def test_prediction_lags_ignore_future_and_cross_year_correctly():
    rows = [invoice("001", 11, 10), invoice("001", 12, 20), invoice("001", 1, 999, year=2026)]
    lags = compute_lags(pd.DataFrame(rows), "A", year=2026, month=1, account="001")
    assert lags["lag_1"] == 20
    assert lags["lag_2"] == 10


def test_prediction_abstains_when_previous_calendar_month_missing():
    rows = [invoice("001", 1, 10), invoice("001", 2, 20)]
    with pytest.raises(RuntimeError, match="anterior"):
        compute_lags(pd.DataFrame(rows), "A", year=2025, month=4, account="001")


def test_metrics_count_zeros_in_wape_and_expose_mape_denominator():
    m = regression_metrics([0, 100], [20, 110])
    assert m["wape"] == 30
    assert m["mape_n"] == 1
    assert regression_metrics([0, 0], [1, 2])["wape"] is None


def test_temporal_split_does_not_use_sparse_tail_as_main_test():
    rows = [invoice(a, m, m*10) for a in ["001", "002", "003"] for m in range(1, 13)]
    rows.append(invoice("001", 1, 100, year=2026))
    train, test = temporal_split(build_features(rows), n_test_periods=2)
    assert test.periodo.dt.strftime("%Y-%m").unique().tolist() == ["2025-11", "2025-12"]
    assert train.periodo.max() == pd.Timestamp("2025-10-01")


def test_nonfinite_consumption_cannot_become_a_target_or_lag():
    f = build_features([invoice('001', 1, 10), invoice('001', 2, 'inf'), invoice('001', 3, 30)])
    assert f.empty


def test_causal_gap_uses_only_past_and_stops_after_three_months():
    rows = [invoice('001', 1, 10), invoice('001', 2, 20), invoice('001', 4, 40), invoice('001', 8, 80)]
    f = build_features(rows, gap_policy='causal')
    april = f[f.Mes.eq(4)].iloc[0]
    assert april.target_consumo == 40
    assert april.lag_1 == 20
    assert april.lag_1_missing == 1
    assert april.history_age == 2
    assert 8 not in f.Mes.tolist()


def test_questionable_consumption_exclusion_also_removes_it_from_lags():
    rows = [invoice('001', 1, 10), invoice('001', 2, 999), invoice('001', 3, 30)]
    rules = {'questionable_consumption': [{'account': '001', 'invoice': '001-2025-2'}]}
    assert build_features(rows, quality_rules=rules).empty
    f = build_features(rows, include_flagged=True, quality_rules=rules)
    assert f[f.Mes.eq(3)].iloc[0].lag_1 == 999


def test_future_conflicting_invoice_does_not_rewrite_past_features():
    rows = [invoice('001', m, m*10) for m in [1, 2, 3, 4]]
    rows.append(invoice('001', 12, 999, number='001-2025-2'))
    f = build_features(rows)
    assert f[f.Mes.eq(3)].iloc[0].lag_1 == 20
    assert 12 not in f.Mes.tolist()
