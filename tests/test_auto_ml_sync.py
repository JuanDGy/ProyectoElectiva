"""Regresión para selección automática y sincronización de predicciones."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.prediction_sync import (
    MAIN_SHEET, CURRENT_HEADERS, _equivalent, plan_sync,
    sync_predictions,
)
from ml.publish import HEADERS
from ml.forecast_estimator import ForecastEstimator
from ml.train_auto import choose_configurations


def make_forecast(account="00123", model="model-new", kwh=100., state="Experimental"):
    return pd.DataFrame([[
        "2026-11-19", "CAMPUS", account, 2026, 2,
        kwh, model, 2, "causal",
    ]], columns=HEADERS)


def existing(account="00123", model="old", value="99,5", digest="sha-old"):
    d = dict(zip(HEADERS, [
        "2026-11-18", "CAMPUS", account, "2026", "2",
        value, model, "2", "causal",
    ]))
    d.update({"Snapshot SHA-256": digest, "Estado modelo": "Experimental"})
    return d


def test_upsert_replaces_other_model_and_preserves_previous():
    p = plan_sync(make_forecast(kwh=100), [existing()], "sha-new", "Experimental")
    assert len(p["updates"]) == 1
    assert p["updates"][0][0] == 2
    assert p["updates"][0][1][2] == "00123"
    assert p["updates"][0][1][5] == 100
    assert len(p["archive"]) == 1
    assert p["archive"][0][6] == "old"
    assert p["archive"][0][5] == "99,5"
    assert p["adds"] == []


def test_upsert_adds_account_without_dropping_other_accounts():
    p = plan_sync(make_forecast("00005"), [existing()], "sha-new", "Experimental")
    assert len(p["adds"]) == 1
    assert len(p["updates"]) == 0
    assert len(p["archive"]) == 0


def test_upsert_is_idempotent_despite_date_and_decimal_format():
    previous = existing(model="model-new", value="100,000", digest="sha-new")
    p = plan_sync(make_forecast(kwh=100), [previous], "sha-new", "Experimental")
    assert not p["updates"] and not p["adds"] and not p["archive"]
    assert p["unchanged"] == 1


def test_upsert_rejects_duplicate_current_keys_and_does_not_modify():
    with pytest.raises(ValueError, match="dos filas"):
        plan_sync(make_forecast(), [existing(), existing(model="another")],
                  "new", "Experimental")
    with pytest.raises(ValueError, match="repetida"):
        plan_sync(pd.concat([make_forecast(), make_forecast()]), [], "new", "Experimental")


def test_number_is_locale_aware():
    a = existing(model="model-new", value="1.234,500", digest="sha-new")
    b = dict(a, **{"Consumo predicho (kWh)": 1234.5})
    assert _equivalent(a, b)


class FakeBatch:
    def __init__(self, log, payload=None):
        self.log, self.payload = log, payload
    def execute(self):
        self.log.append(("execute", self.payload))
        return {}


class FakeValues:
    def __init__(self, log):
        self.log = log
    def batchUpdate(self, **kwargs):
        self.log.append(("replace", kwargs))
        return FakeBatch(self.log)


class FakeApi:
    def __init__(self, log):
        self.log = log
    def spreadsheets(self):
        return self
    def values(self):
        return FakeValues(self.log)


class FakeSheet:
    def __init__(self):
        self.calls = []
        self.api = FakeApi(self.calls)
        self.spreadsheet_id = "test-id"
    def ensure_sheet(self, name):
        self.calls.append(("ensure", name))
    def get_rows(self, name, **kwargs):
        return [] if name == "Historial_Predicciones" else [existing()]
    def ensure_headers(self, name, headers):
        self.calls.append(("headers", name, headers))
    def append_rows(self, name, data, headers=None):
        self.calls.append(("append", name, data, headers))
    def format_predictions_sheet(self, name, headers):
        self.calls.append(("format", name))
    def format_predictions_history(self, name):
        self.calls.append(("format-history", name))


def test_archive_precedes_live_replace():
    sheet = FakeSheet()
    result = sync_predictions(sheet, MAIN_SHEET, make_forecast(), "sha-new", "Experimental")
    assert result["updates"] == result["archive"] == 1
    archive_index = next(i for i, item in enumerate(sheet.calls)
                         if item[0] == "append" and item[1] == "Historial_Predicciones")
    replace_index = next(i for i, item in enumerate(sheet.calls) if item[0] == "replace")
    assert archive_index < replace_index
    assert len(sheet.calls[replace_index][1]["body"]["data"]) == 1


class TinyEstimator:
    def predict(self, x):
        return np.array([5.0, -500.0])


def test_wrapper_applies_delta_and_clips_negative_predictions():
    features = pd.DataFrame({"lag_1": [100, 10]})
    m = ForecastEstimator(TinyEstimator(), "delta", ["lag_1"], {})
    assert m.predict(features).tolist() == [105., 0.]


def test_selection_uses_validation_not_test_error():
    entries = [
        {"model": "svr", "approach": "delta", "seasonal": False,
         "params": {"c": 50}, "validation_mae": 35, "test": {"mae": 100}},
        {"model": "svr", "approach": "level", "seasonal": False,
         "params": {"c": 50}, "validation_mae": 65, "test": {"mae": 10}},
        {"model": "rf", "approach": "level", "seasonal": False,
         "params": {"max_depth": 4}, "validation_mae": 60, "test": {"mae": 1}},
    ]
    selected = choose_configurations({"results": entries}, {"svr", "rf"})
    assert selected["svr"]["approach"] == "delta"
    assert selected["svr"]["test"]["mae"] == 100


def test_selection_reports_missing_families():
    with pytest.raises(RuntimeError, match="Faltan"):
        choose_configurations({"results": []}, {"svr"})


def test_version_id_is_stable_on_retry():
    original = existing()
    a = plan_sync(make_forecast(), [original], "sha-new", "Experimental")
    b = plan_sync(make_forecast(), [original], "sha-new", "Experimental")
    assert len(a["archive"][0][-1]) == 64
    assert a["archive"][0][-1] == b["archive"][0][-1]
