"""Cobertura por cuenta y chequeo automático de cambios de BD_energia."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from ml.bd_monitor import fingerprint, monitor_once
from ml.dataset import DATASET_VERSION, FEATURES
from ml.publish import build_predictions
from ml.prediction_sync import plan_sync, CURRENT_HEADERS


class FakeEstimator:
    ecodata_spec_ = {
        "dataset_version": DATASET_VERSION, "features": FEATURES,
        "trained_accounts": ["0001"], "gap_policy": "causal",
        "include_flagged": False, "quality_rules": {},
    }
    def predict(self, features):
        return [75.0]


def example_rows():
    return pd.DataFrame([
        {"Sede": "Campus", "Cuenta": "0001", "Año": 2025, "Mes": m,
         "Consumo": 10.0 * m}
        for m in range(1, 13)
    ] + [
        {"Sede": "Bodega", "Cuenta": "0002", "Año": 2025, "Mes": 12,
         "Consumo": 20.0},
        {"Sede": "Depósito", "Cuenta": "0003", "Año": 2025, "Mes": 5,
         "Consumo": 3.0},
    ])


def test_all_accounts_visible_with_reason_and_no_fabricated_kwh():
    f, summary = build_predictions(example_rows(), FakeEstimator(), "auto",
                                   2026, 1, include_unavailable=True)
    assert summary["publicadas"] == 1
    assert summary["sin_entrenamiento"] == 2
    assert summary["sin_prediccion"] == 2
    assert len(f) == 3
    indexed = f.set_index("Cuenta")
    assert indexed.loc["0001", "Consumo predicho (kWh)"] == 75
    assert indexed.loc["0001", "Estado predicción"] == "PRONOSTICADA"
    for account in ("0002", "0003"):
        assert indexed.loc[account, "Consumo predicho (kWh)"] == ""
        assert indexed.loc[account, "Estado predicción"] == "CUENTA_NO_ENTRENADA"
    assert all(str(x).startswith("000") for x in indexed.index)


def test_unavailable_account_is_added_then_replaced_when_it_can_predict():
    frame, _ = build_predictions(example_rows(), FakeEstimator(), "auto",
                                 2026, 1, include_unavailable=True)
    old = []
    first = plan_sync(frame, old, "sha1", "Experimental")
    assert len(first["adds"]) == 3
    assert len(first["archive"]) == 0
    existing = [dict(zip(CURRENT_HEADERS, row)) for row in first["adds"]]
    p = frame.copy()
    idx = p.index[p["Cuenta"] == "0002"][0]
    p.loc[idx, "Consumo predicho (kWh)"] = 26.0
    p.loc[idx, "Estado predicción"] = "PRONOSTICADA"
    second = plan_sync(p, existing, "sha1", "Experimental")
    assert len(second["updates"]) == 1
    assert len(second["archive"]) == 1
    assert second["archive"][0][2] == "0002"


def test_unchanged_status_does_not_duplicate_history():
    frame, _ = build_predictions(example_rows(), FakeEstimator(), "auto",
                                 2026, 1, include_unavailable=True)
    original = plan_sync(frame, [], "sha1", "Experimental")["adds"]
    existing = [dict(zip(CURRENT_HEADERS, row)) for row in original]
    p = plan_sync(frame, existing, "sha1", "Experimental")
    assert len(p["updates"]) == len(p["adds"]) == len(p["archive"]) == 0
    assert p["unchanged"] == 3


class FakeSheets:
    def __init__(self):
        self.rows = [{"ID": "fact-1", "Cuenta": "0001", "kWh": 23}]
    def get_rows(self, name, **kwargs):
        assert name == "BD_energia"
        return list(self.rows)


def settings(monkeypatch):
    import ml.bd_monitor as module
    monkeypatch.setattr(module, "get_ml_settings", lambda: SimpleNamespace(
        sheet_bd_energy="BD_energia", google_client_secret_file="unused",
        google_token_file="unused", spreadsheet_id="unused",
    ))


def test_monitor_only_refreshes_after_change(tmp_path, monkeypatch):
    settings(monkeypatch)
    db = FakeSheets()
    state, lock = tmp_path/"state.json", tmp_path/"sync.lock"
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert "ml.workflow" in command
        assert "--completo" in command and "--publicar" in command

    result = monitor_once(service=db, runner=runner,
                          state_path=state, lock_path=lock)
    assert result["status"] == "actualizado"
    assert len(calls) == 1
    assert state.exists()
    result = monitor_once(service=db, runner=runner,
                          state_path=state, lock_path=lock)
    assert result["status"] == "sin_cambios"
    assert len(calls) == 1
    db.rows.append({"ID": "fact-2", "Cuenta": "0002", "kWh": 34})
    result = monitor_once(service=db, runner=runner,
                          state_path=state, lock_path=lock)
    assert result["status"] == "actualizado"
    assert len(calls) == 2


def test_monitor_does_not_mark_failure_as_success(tmp_path, monkeypatch):
    settings(monkeypatch)
    db = FakeSheets()
    state, lock = tmp_path/"state.json", tmp_path/"sync.lock"

    def failing(*args, **kwargs):
        raise subprocess.CalledProcessError(2, "ml.workflow")

    with pytest.raises(subprocess.CalledProcessError):
        monitor_once(service=db, runner=failing, state_path=state, lock_path=lock)
    assert not state.exists()
    assert monitor_once(service=db, check_only=True, state_path=state,
                        lock_path=lock)["status"] == "cambios_pendientes"


def test_monitor_does_not_acknowledge_changes_during_training(tmp_path, monkeypatch):
    settings(monkeypatch)
    db = FakeSheets()
    state, lock = tmp_path/"state.json", tmp_path/"sync.lock"

    def modifying(*args, **kwargs):
        db.rows.append({"ID": "new", "Cuenta": "0009", "kWh": 10})

    with pytest.raises(RuntimeError, match="durante"):
        monitor_once(service=db, runner=modifying,
                     state_path=state, lock_path=lock)
    assert not state.exists()


def test_fingerprint_independent_of_sheet_row_order():
    a = [{"Cuenta": "001", "kWh": 20}, {"Cuenta": "002", "kWh": 10}]
    assert fingerprint(a) == fingerprint(list(reversed(a)))
    assert fingerprint(a) != fingerprint(a + [{"Cuenta": "003", "kWh": 2}])
