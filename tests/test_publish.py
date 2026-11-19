import pandas as pd

from ml.dataset import DATASET_VERSION, FEATURES
from ml.publish import HEADERS, build_predictions, default_target, model_id, new_rows


def history() -> pd.DataFrame:
    rows = []
    for month in (1, 2, 3):
        rows.append({"Sede": "CAMPUS", "Cuenta": "A", "Año": 2025, "Mes": month, "Consumo": 100 * month})
        rows.append({"Sede": "BODEGAS", "Cuenta": "B", "Año": 2025, "Mes": month, "Consumo": 50 * month})
    rows.append({"Sede": "CAMPUS", "Cuenta": "C", "Año": 2025, "Mes": 4, "Consumo": 10})
    return pd.DataFrame(rows)


class FakeModel:
    ecodata_spec_ = {
        "dataset_version": DATASET_VERSION,
        "features": FEATURES,
        "trained_accounts": ["A"],
        "gap_policy": "strict",
        "include_flagged": False,
        "quality_rules": {},
    }

    def predict(self, features):
        return [321.5]


def test_default_target_is_the_first_month_without_any_observation():
    frame = pd.DataFrame({"Año": [2025, 2025, 2026], "Mes": [11, 12, 1], "Cuenta": ["a", "b", "c"]})
    assert default_target(frame) == (2026, 2)


def test_build_predictions_reports_observed_unknown_and_published_accounts():
    rows, summary = build_predictions(history(), FakeModel(), "modelo_x", 2025, 4)
    assert summary == {"publicadas": 1, "observadas": 1, "abstenciones": 0, "sin_entrenamiento": 1}
    assert list(rows["Cuenta"]) == ["A"]
    assert rows.loc[0, "Consumo predicho (kWh)"] == 321.5
    assert rows.loc[0, "Sede"] == "CAMPUS"
    assert rows.loc[0, "Modelo"] == "modelo_x"


def test_build_predictions_never_uses_the_target_month_itself():
    frame = pd.concat([history(), pd.DataFrame([
        {"Sede": "CAMPUS", "Cuenta": "A", "Año": 2025, "Mes": 4, "Consumo": 9999},
    ])], ignore_index=True)
    rows, summary = build_predictions(frame, FakeModel(), "m", 2025, 4)
    assert summary == {"publicadas": 0, "observadas": 2, "abstenciones": 0, "sin_entrenamiento": 1}
    assert rows.empty


def test_model_id_uses_the_candidate_folder():
    assert model_id("models/candidates/rf_20260101_000000_abcdef/model.joblib") == "rf_20260101_000000_abcdef"


def test_new_rows_skips_rows_already_published():
    frame = pd.DataFrame([
        ["2026-11-18", "CAMPUS", "A", 2026, 2, 1.0, "m1", 1, "causal"],
        ["2026-11-18", "CAMPUS", "B", 2026, 2, 2.0, "m1", 1, "causal"],
    ], columns=HEADERS)
    existing = [{"Cuenta": "A", "Año": "2026", "Mes": "2", "Modelo": "m1"}]
    fresh = new_rows(frame, existing)
    assert list(fresh["Cuenta"]) == ["B"]


def test_new_rows_counts_a_different_model_as_a_new_forecast():
    frame = pd.DataFrame([
        ["2026-11-18", "CAMPUS", "A", 2026, 2, 1.0, "m2", 1, "causal"],
    ], columns=HEADERS)
    existing = [{"Cuenta": "A", "Año": "2026", "Mes": "2", "Modelo": "m1"}]
    assert len(new_rows(frame, existing)) == 1
