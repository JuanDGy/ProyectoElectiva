"""Pruebas sin red: nuevos modelos, gráficos nativos y selecciones de predicciones."""
import numpy as np
import pandas as pd
from ml.neural_models import LSTMRegressor, RBFRegressor
from ml.sheets_dashboard import build_dashboard, select_forecasts
from scripts.experiment_models import _make


def test_rbf_constant():
    x = np.array([[1., 2.], [2., 3.], [3., 4.], [4., 5.]])
    reg = RBFRegressor(centers=2).fit(x, [7., 7., 7., 7.])
    assert np.isfinite(reg.predict(x)).all()
    assert np.allclose(reg.predict(x), 7., atol=.2)


def test_lstm_two_ordered_lags():
    x = np.array([[10., 9., 0.], [11., 10., 1.], [12., 11., 0.], [13., 12., 1.]])
    reg = LSTMRegressor(hidden_size=4, epochs=3).fit(x, [10., 11., 12., 13.])
    assert np.isfinite(reg.predict(x)).all()
    a, context = reg._inputs(x)
    assert np.array_equal(a[:, 0, 0], x[:, 1])
    assert np.array_equal(a[:, 1, 0], x[:, 0])
    assert context.shape == (4, 1)


def test_competition_model_families():
    sample = pd.DataFrame({
        "Cuenta": ["a", "a", "b", "b"],
        "lag_1": [1., 2., 3., 4.], "lag_2": [1., 1., 2., 3.],
        "rolling_3": [1., 1., 2., 3.], "history_count_3": [1.] * 4,
        "lag_1_missing": [0.] * 4, "lag_2_missing": [0.] * 4,
        "history_age": [1.] * 4, "month_sin": [0.] * 4, "month_cos": [1.] * 4,
    })
    for name, params in [
        ("rbf", {"centers": 2, "gamma": .1}),
        ("mlp", {"hidden": 4}), ("svr", {"c": 1.}),
        ("lstm", {"hidden": 4, "epochs": 2}),
    ]:
        model = _make(name, False, **params)
        model.fit(sample, [10., 11., 12., 13.])
        assert np.isfinite(model.predict(sample)).all(), name


def test_forecast_selection_locale_and_multiple_models():
    rows = [
        {"Cuenta": "001", "Año": "2026", "Mes": "2", "Modelo": "rf", "Consumo predicho (kWh)": "1.190,909"},
        {"Cuenta": "002", "Año": "2026", "Mes": "2", "Modelo": "rf", "Consumo predicho (kWh)": "12,5"},
        {"Cuenta": "001", "Año": "2026", "Mes": "2", "Modelo": "lstm", "Consumo predicho (kWh)": "100"},
        {"Cuenta": "001", "Año": "2026", "Mes": "1", "Modelo": "rf", "Consumo predicho (kWh)": "200"},
    ]
    forecasts, info = select_forecasts(rows)
    assert info == (2026, 2, "rf")
    assert len(forecasts) == 2
    assert forecasts.iloc[0]["Consumo predicho (kWh)"] == 1190.909


def test_dashboard_builds_native_google_specs():
    history = pd.DataFrame([
        {"Oficina": "Sede A", "Cuenta": "001", "N° Factura": "X1", "Año": 2025, "Mes": 12, "kWh": 50},
        {"Oficina": "Sede A", "Cuenta": "001", "N° Factura": "X2", "Año": 2026, "Mes": 1, "kWh": 55},
    ])
    forecasts, info = select_forecasts([{
        "Cuenta": "001", "Año": 2026, "Mes": 2,
        "Modelo": "m", "Consumo predicho (kWh)": 57,
    }])
    rows, charts, metadata = build_dashboard(history, forecasts, info, 123)
    assert metadata["cuentas"] == 1
    assert len(charts) == 3
    assert rows[4][0] == "Mes"
    assert any(row[:3] == ["2026-02", "", 57.] for row in rows)
    assert [chart["spec"]["basicChart"]["chartType"] for chart in charts] == ["LINE", "BAR", "COLUMN"]
    assert charts[0]["spec"]["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]["sheetId"] == 123


def test_llm_evidence_must_be_verifiable_in_ocr():
    from tests.test_pending import DATA, TEXT
    from pipeline.pending import validate
    bad = [{"campo": "kwh", "cita": "CONSUMO MES: 999 kWh"}]
    errors = validate(dict(DATA), TEXT, None, ["kWh"], bad)
    assert any("no verificable" in e for e in errors)


def test_apply_rejects_stale_drive_location(tmp_path):
    from tests.test_pending import FakeDrive, FakeSheets, proposal, settings
    from pipeline.pending import apply_proposals

    class StaleDrive(FakeDrive):
        def get_metadata(self, file_id):
            return {"id": file_id, "name": "CAMBIADO.pdf", "parents": ["otra-carpeta"]}

    drive, sheet = StaleDrive(), FakeSheets()
    result = apply_proposals([proposal()], tmp_path / "actions.jsonl",
                             drive=drive, sheets=sheet, settings=settings())
    assert result["omitidas"] == 1
    assert drive.moved == []
    assert sheet.written == []


def test_apply_compensates_when_sheet_write_fails(tmp_path):
    from tests.test_pending import FakeDrive, FakeSheets, proposal, settings
    from pipeline.pending import apply_proposals

    class BrokenSheet(FakeSheets):
        def append(self, *args, **kwargs):
            raise ConnectionError("Sheets unavailable")

    drive, sheet = FakeDrive(), BrokenSheet()
    result = apply_proposals([proposal()], tmp_path / "actions.jsonl",
                             drive=drive, sheets=sheet, settings=settings())
    assert result["omitidas"] == 1
    assert result["aplicadas"] == 0
    assert drive.moved == [("file-1", "folder::APTO ARRIENDO"), ("file-1", "01c")]
    assert drive.renamed[-1][1] == proposal()["name"]
    log = (tmp_path / "actions.jsonl").read_text()
    assert "ERROR_APLICACION" in log and "restaurado" in log
