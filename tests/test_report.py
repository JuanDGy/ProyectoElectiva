import json

import pandas as pd

from ml import report


def _candidate(tmp_path):
    folder = tmp_path / "cand"
    folder.mkdir()

    history = []
    for month in range(1, 13):
        history.append({"Sede": "CAMPUS", "Cuenta": "A", "Año": 2025, "Mes": month,
                        "Consumo": 100 + month, "periodo": f"2025-{month:02d}-01"})
    for month in (11, 12):
        history.append({"Sede": "BODEGAS", "Cuenta": "B", "Año": 2025, "Mes": month,
                        "Consumo": 500 + month, "periodo": f"2025-{month:02d}-01"})
    pd.DataFrame(history).to_csv(folder / "accepted_history.csv", index=False)

    pd.DataFrame([
        {"Cuenta": "A", "Sede": "CAMPUS", "periodo": "2025-11-01", "target_consumo": 111.0,
         "lag_1_missing": 0, "history_age": 1.0, "prediction": 120.0,
         "persistence": 110.0, "rolling_mean": 105.0},
        {"Cuenta": "B", "Sede": "BODEGAS", "periodo": "2025-12-01", "target_consumo": 512.0,
         "lag_1_missing": 0, "history_age": 1.0, "prediction": 400.0,
         "persistence": 511.0, "rolling_mean": 505.0},
    ]).to_csv(folder / "test_predictions.csv", index=False)

    pd.DataFrame({"periodo": ["2025-11-01", "2025-12-01"], "Cuenta": ["A", "B"]}).to_csv(
        folder / "test.csv", index=False)
    (folder / "quality_report.json").write_text(json.dumps({
        "common_test_coverage_pct": 78.4,
        "policy_prediction_coverage_pct": 90.9,
        "policy_test_predictions": 80,
        "policy_abstentions": 8,
    }), encoding="utf-8")
    (folder / "model.joblib").write_bytes(b"placeholder")
    return folder


class FakeModel:
    ecodata_spec_ = {"dataset_version": "account-calendar-v1", "gap_policy": "causal"}


def test_build_writes_html_and_all_charts(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    out_root = tmp_path / "out"
    out_root.mkdir()
    (out_root / "comparison_test.json").write_text(json.dumps({"candidates": [{
        "id": "random_forest_1", "model_type": "random_forest",
        "metrics": {"mae": 420.0, "wape": 22.0}, "promotion_eligible": False,
        "baseline_metrics": {"persistence": {"mae": 454.74}},
    }]}), encoding="utf-8")
    monkeypatch.setattr(report, "REPORTS", out_root)
    monkeypatch.setattr(report.joblib, "load", lambda path: FakeModel())
    monkeypatch.setattr(report, "latest_snapshot", lambda: {"file": "x.csv"})
    monkeypatch.setattr(report.pd, "read_csv", _fake_read_csv(candidate))
    monkeypatch.setattr(report.publish, "build_predictions", lambda *args, **kwargs: (
        pd.DataFrame([
            ["2026-11-18", "CAMPUS", "A", 2026, 2, 130.0, "m", 2, "causal"],
        ], columns=["Fecha ejecución", "Sede", "Cuenta", "Año", "Mes",
                    "Consumo predicho (kWh)", "Modelo", "Antigüedad (meses)", "Política"]),
        {"publicadas": 1, "observadas": 0, "abstenciones": 0, "sin_entrenamiento": 0},
    ))
    monkeypatch.setattr(report.publish, "default_target", lambda frame: (2026, 2))

    out = report.build(candidate)

    assert (out / "index.html").exists()
    for name in ("modelo_vs_persistencia.png", "errores.png", "cobertura.png", "series_cuentas.png"):
        assert (out / name).stat().st_size > 0
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Reporte de consumo" in html
    assert "CAMPUS" in html


def _fake_read_csv(candidate):
    original = pd.read_csv
    frames = {
        "x.csv": pd.DataFrame([
            {"Sede": "CAMPUS", "Cuenta": "A", "Año": 2025, "Mes": 12, "Consumo": 112},
        ]),
    }

    def read_csv(path, *args, **kwargs):
        text = str(path)
        if text in frames:
            return frames[text]
        return original(path, *args, **kwargs)

    return read_csv


def test_read_json_falls_back_to_cp1252(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_bytes('{"sede": "INYECCIÓN"}'.encode("cp1252"))
    assert report._read_json(path)["sede"] == "INYECCIÓN"


def test_table_escapes_html():
    frame = pd.DataFrame([{"Cuenta": "<b>A</b>", "kWh": 1.5}])
    html = report._table(frame, ["Cuenta", "kWh"])
    assert "&lt;b&gt;" in html
    assert "1.50" in html
