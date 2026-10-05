"""Tests offline del ciclo MLOps (sin credenciales de Google)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ml.dataset import (
    build_features,
    normalize_columns,
    regression_metrics,
    temporal_split,
    validate_dataset,
)
from ml.registry import add_challenger, get_champion, load_registry, promote
from ml.snapshot import latest_snapshot, take_snapshot


def _history(sedes=("A", "B"), months=10) -> list[dict]:
    rows = []
    for sede in sedes:
        base = 100 if sede == "A" else 200
        for i in range(months):
            year, month = 2025, i + 1
            if month > 12:
                year, month = 2026, month - 12
            rows.append({
                "Oficina": sede,
                "Año": year,
                "Mes": month,
                "kWh": base + i * 5,
            })
    return rows


def test_normalize_columns_spanish_headers():
    df = normalize_columns(pd.DataFrame(_history()))
    assert {"Sede", "Año", "Mes", "Consumo"} <= set(df.columns)


def test_validate_missing_columns_raises():
    with pytest.raises(RuntimeError, match="Faltan columnas"):
        validate_dataset(pd.DataFrame([{"Sede": "A"}]))


def test_validate_warns_duplicates():
    rows = _history(months=4)
    rows.append(dict(rows[0]))
    warnings = validate_dataset(normalize_columns(pd.DataFrame(rows)))
    assert any("duplicadas" in w for w in warnings)


def test_build_features_lags_and_target():
    df = build_features(_history(months=12))
    assert {"lag_1", "lag_2", "rolling_3", "target_consumo"} <= set(df.columns)
    assert not df[["lag_1", "lag_2", "rolling_3", "target_consumo"]].isna().any().any()
    first_a = df[df["Sede"] == "A"].iloc[0]
    assert first_a["Mes"] == 2
    assert first_a["lag_1"] == 100
    assert first_a["lag_2_missing"] == 1
    assert first_a["rolling_3"] == 100
    assert first_a["target_consumo"] == 105


def test_temporal_split_uses_latest_periods():
    df = build_features(_history(months=12))
    train_df, test_df = temporal_split(df, n_test_periods=2)
    assert test_df["periodo"].min() > train_df["periodo"].max()
    assert len(train_df) >= 12
    assert not test_df.empty


def test_regression_metrics_include_mape():
    metrics = regression_metrics([100.0, 200.0, 300.0], [110.0, 190.0, 300.0])
    assert {"mae", "rmse", "r2", "mape", "wape", "n", "mape_n"} == set(metrics)
    assert metrics["mape"] == pytest.approx((10 + 5 + 0) / 3, rel=1e-6)
    assert metrics["mae"] == pytest.approx(20 / 3, rel=1e-6)


def test_regression_metrics_zero_consumption_no_crash():
    metrics = regression_metrics([0.0, 100.0], [0.0, 110.0])
    assert metrics["mape"] == pytest.approx(10.0, rel=1e-6)


def test_snapshot_roundtrip(tmp_path: Path):
    entry = take_snapshot(
        _history(months=4), raw_dir=tmp_path / "raw", manifest=tmp_path / "manifest.jsonl"
    )
    assert Path(entry["file"]).exists()
    assert len(entry["md5"]) == 32
    latest = latest_snapshot(raw_dir=tmp_path / "raw", manifest=tmp_path / "manifest.jsonl")
    assert latest["md5"] == entry["md5"]


def _candidate(cid: str, mape: float, model_file: Path) -> dict:
    return {
        "id": cid, "model_type": "linear", "model_path": model_file.as_posix(),
        "features": ["Mes"], "metrics": {"mae": 1.0, "rmse": 1.0, "r2": 0.9, "mape": mape},
        "train_rows": 12, "test_rows": 4, "test_periods": [],
        "data_snapshot": "x.csv", "data_md5": "abc",
        "baseline_per_sede": {}, "timestamp": "2026-01-01T00:00:00", "git_sha": "abc",
        "promotion_eligible": True, "evaluation_id": "same-test",
        "baseline_metrics": {"persistence": {"mae": 20}, "rolling_mean": {"mae": 30}},
    }


def test_registry_promote_flow(tmp_path: Path, monkeypatch):
    import ml.registry as registry_mod

    monkeypatch.setattr(registry_mod, "CHAMPION_DIR", tmp_path / "champion")
    reg_path = tmp_path / "registry.json"
    model_a = tmp_path / "a.joblib"
    model_b = tmp_path / "b.joblib"
    model_a.write_bytes(b"a")
    model_b.write_bytes(b"b")

    add_challenger(_candidate("run_a", 10.0, model_a), path=reg_path)
    promoted, _ = promote("run_a", metric="mape", path=reg_path)
    assert promoted
    assert get_champion(path=reg_path)["id"] == "run_a"

    add_challenger(_candidate("run_b", 5.0, model_b), path=reg_path)
    promoted, reason = promote("run_b", metric="mape", path=reg_path)
    assert promoted
    assert "10.0000 -> 5.0000" in reason
    assert get_champion(path=reg_path)["id"] == "run_b"

    add_challenger(_candidate("run_c", 50.0, model_a), path=reg_path)
    promoted, _ = promote("run_c", metric="mape", path=reg_path)
    assert not promoted
    assert get_champion(path=reg_path)["id"] == "run_b"

    saved = json.loads((tmp_path / "champion" / "metadata.json").read_text(encoding="utf-8"))
    assert saved["id"] == "run_b"
    assert load_registry(reg_path)["champion"]["id"] == "run_b"
