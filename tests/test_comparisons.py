"""Pruebas sin credenciales de las dos comparativas y su publicación idempotente."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ml.comparisons import (
    MODELS_SHEET, OCR_SHEET, _assert_same_holdout, models_comparison,
    ocr_comparison, publish,
)
from services.sheets import SheetsService


FIELDS = ("Cuenta", "NumeroFactura", "kWh", "ValorFacturado",
          "InicioPeriodo", "FinPeriodo")


def make_ocr():
    def method(correct):
        return {
            "fila_completa": correct / 15,
            **{field: {"aciertos": correct, "errores": 15 - correct,
                       "faltantes": 0, "precision": correct / 15}
               for field in FIELDS},
        }
    return {"generado": "2026-11-18T15:00:00", "semilla": 7,
            "resumen": {
                "muestra": 15, "verificadas_a_mano": 4,
                "segundos_ocr_media": 8.08, "segundos_llm_media": 3.67,
                "ocr": method(15), "llm": method(11), "llm_guard": method(14),
                "ocr_manual": {"fila_completa": 1.0},
                "llm_manual": {"fila_completa": 0.0},
                "llm_guard_manual": {"fila_completa": 0.75},
            }}


def make_experiment(model, entries, n=69):
    base = {"persistence": {"mae": 454.74, "rmse": 1490., "wape": 24.8,
                            "r2": .93, "n": n},
            "rolling_mean": {"mae": 474., "rmse": 1500., "wape": 25.0,
                             "r2": .92, "n": n}}
    return {
        "source": "data/raw/history.csv", "test_rows": n,
        "validation_periods": ["2025-09", "2025-10"],
        "baselines": base,
        "results": [
            {"model": model, "approach": f"mode-{i}", "seasonal": False,
             "params": {"id": i}, "validation_mae": cv,
             "test": {"mae": mae, "rmse": mae + 10, "wape": 12.0,
                      "r2": 0.5, "n": n},
             "vs_persistence": {"p_better": 0.25},
             "significant": False}
            for i, (cv, mae) in enumerate(entries)],
    }


def test_ocr_summary_and_field_tables_are_from_actual_report_values():
    rows, charts, heads, summary = ocr_comparison(make_ocr(), "test.json", 231)
    assert summary["muestra"] == 15 and summary["manual"] == 4
    assert rows[6][3] == 100
    assert rows[7][3] == round(11 / 15 * 100, 2)
    assert rows[8][3] == round(14 / 15 * 100, 2)
    assert rows[7][4] == 3.67
    assert len(rows[12:18]) == 6
    assert rows[21][2] == 100
    assert len(charts) == 2
    assert heads == [5, 11, 20]
    for chart in charts:
        data = chart["spec"]["basicChart"]
        assert data["chartType"] == "BAR"
        assert data["domains"][0]["domain"]["sourceRange"]["sources"][0]["sheetId"] == 231
        assert all(s["targetAxis"] == "BOTTOM_AXIS" for s in data["series"])


def test_selection_uses_validation_not_test_results():
    classic = make_experiment("rf", [(200, 300), (220, 10)])
    neural = make_experiment("lstm", [(210, 500)])
    rows, charts, headers, summary = models_comparison(
        classic, neural, "classical.json", "neural.json", sheet_id=44)
    assert summary["configuraciones"] == 3
    assert summary["familias"] == 2
    assert summary["mejor_por_cv"]["rf"]["mae_test"] == 300
    assert summary["referencia_mae"] == 454.74
    assert rows[6][0] == "Persistencia"
    assert rows[6][4] == 454.74
    assert any(len(row) > 4 and row[0] == "Random Forest" and row[4] == 300 for row in rows)
    assert len(charts) == 2
    assert len(headers) == 2
    assert charts[0]["spec"]["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]["sheetId"] == 44


def test_comparison_refuses_different_test_size_or_source():
    classical = make_experiment("rf", [(200, 100)], n=71)
    neural = make_experiment("lstm", [(200, 200)], n=69)
    with pytest.raises(ValueError, match="test_rows"):
        _assert_same_holdout(classical, neural)
    neural["test_rows"] = 71
    neural["baselines"] = classical["baselines"]
    neural["source"] = "otra/historia.csv"
    with pytest.raises(ValueError, match="fuentes"):
        _assert_same_holdout(classical, neural)


def test_publish_preview_does_not_connect_to_google(monkeypatch, tmp_path):
    files = {}
    for key, payload in [
        ("ocr", make_ocr()),
        ("classic", make_experiment("rf", [(120, 140)])),
        ("neural", make_experiment("lstm", [(130, 150)])),
    ]:
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        files[key] = path
    def forbidden():
        raise AssertionError("Una vista previa no puede pedir credenciales")
    monkeypatch.setattr("ml.comparisons.get_ml_settings", forbidden)
    results = publish(False, "all", files["ocr"], files["classic"], files["neural"])
    assert OCR_SHEET in results and MODELS_SHEET in results


class FakeRequest:
    def __init__(self, result=None):
        self.result = result or {}
    def execute(self):
        return self.result


class FakeAPI:
    def __init__(self):
        self.calls = []
    def spreadsheets(self):
        return self
    def values(self):
        return self
    def clear(self, **kwargs):
        self.calls.append(("clear", kwargs))
        return FakeRequest()
    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return FakeRequest()
    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return FakeRequest()
    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return FakeRequest({"sheets": [{"properties": {"sheetId": 99},
                                       "merges": []}]})


def test_google_publisher_only_targets_reserved_sheet_and_updates_existing_chart():
    sheet = object.__new__(SheetsService)
    fake = FakeAPI()
    sheet.api = fake
    sheet.spreadsheet_id = "sheet-test"
    sheet.ensure_sheet = lambda name: False
    title = "EcoData | Comparativa OCR: extracción completa"
    sheet.sheet_chart_info = lambda name: (99, {title: 808})
    values, charts, heads, _ = ocr_comparison(make_ocr(), "file.json", 99)
    result = sheet.replace_comparison(OCR_SHEET, values, charts, heads)
    assert result["charts"] == 2
    assert [kind for kind, _ in fake.calls] == [
        "clear", "update", "get", "batchUpdate",
    ]
    for kind, call in fake.calls[:2]:
        assert call["range"].startswith(f"'{OCR_SHEET}'!")
        assert call["spreadsheetId"] == "sheet-test"
    requests = fake.calls[3][1]["body"]["requests"]
    assert sum("mergeCells" in item for item in requests) >= 4
    assert len([x for x in requests if "updateChartSpec" in x]) == 1
    assert len([x for x in requests if "updateEmbeddedObjectPosition" in x]) == 1
    assert len([x for x in requests if "addChart" in x]) == 1
    assert any("updateCells" in x for x in requests)  # Notas explicativas por métrica
    with pytest.raises(ValueError, match="pestañas"):
        sheet.replace_comparison("BD_energia", values, charts, heads)


def test_combined_experiment_is_counted_once():
    classic = make_experiment("rf_mae", [(210, 320)])
    neural = make_experiment("lstm", [(220, 420)])
    full = dict(classic)
    full["results"] = classic["results"] + neural["results"]
    rows, _, _, summary = models_comparison(
        full, full, "complete.json", "complete.json")
    assert summary["configuraciones"] == 2
    assert summary["familias"] == 2
    assert summary["test_n"] == 69


def test_outdated_registered_models_are_not_compared_to_new_cohort():
    full = make_experiment("rf_mae", [(200, 250)], n=70)
    trained = {"candidates": [{
        "id": "historical-v1", "model_type": "linear",
        "metrics": {"mae": 555, "n": 69},
        "baseline_metrics": {"persistence": {"mae": 454.74}},
    }]}
    rows, _, _, summary = models_comparison(
        full, full, "full.json", "full.json", trained, "old-v1.json")
    assert summary["candidatos_registrados"] == 0
    assert summary["candidatos_no_comparables"] == 1
    exclusions = [row for row in rows if row and str(row[0]).startswith("EXCLUSIONES |")]
    assert len(exclusions) == 1
    assert any(row and row[0] == "historical-v1" and
               "n=69 frente a n=70" in row[1] for row in rows)
    assert not any(row and len(row) > 3 and row[0] == "historical-v1"
                   for row in rows)


def test_report_discovery_prefers_complete_run_not_duplicate(tmp_path):
    from ml.comparisons import default_reports

    def write(name, result):
        file = tmp_path / name
        file.write_text(json.dumps(result), encoding="utf-8")
        return file

    ocr = write("ocr_vs_llm_one.json", make_ocr())
    classical = make_experiment("rf_mae", [(210, 320)], n=69)
    neural = make_experiment("lstm", [(220, 420)], n=69)
    write("experiment_models_classical.json", classical)
    write("experiment_models_neural.json", neural)
    complete = make_experiment("rf_mae",
                               [(205+i, 315+i) for i in range(51)], n=70)
    complete["results"] += make_experiment(
        "lstm", [(215+i, 410+i) for i in range(10)], n=70)["results"]
    combined = write("experiment_models_full.json", complete)
    paths = default_reports(tmp_path)
    assert paths["ocr"] == ocr
    assert paths["classic"] == paths["neural"] == combined
