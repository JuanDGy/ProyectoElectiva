"""El dashboard debe reconocer automáticamente cada nuevo mes publicado."""
from __future__ import annotations

import pandas as pd

from ml.sheets_dashboard import (
    build_dashboard, build_period_sources, collect_model_periods,
)
from services.sheets import SheetsService
from tests.test_account_selector import fixtures


def _forecast(account, year, month, model, value):
    return {"Cuenta": account, "Año": year, "Mes": month,
            "Modelo": model, "Consumo predicho (kWh)": value}


def test_new_month_and_year_are_indexed_without_mixing_models():
    history, _, _ = fixtures()
    rows = [
        _forecast("01234", 2026, 2, "rf", 160),
        _forecast("01234", 2026, 2, "svr", 155),
        _forecast("05678", 2026, 2, "svr", 0),
        _forecast("01234", 2026, 4, "svr", 170),
        _forecast("05678", 2026, 4, "svr", 18),
        _forecast("01234", 2027, 1, "svr", 175),
        _forecast("05678", 2026, 4, "svr", ""),  # abstención histórica
    ]
    models, periods = collect_model_periods(rows)
    assert periods == [(2026, 2), (2026, 4), (2027, 1)]
    assert set(models) == {(2026, 2, "rf"), (2026, 2, "svr"),
                           (2026, 4, "svr"), (2027, 1, "svr")}
    sources, accounts, names, years, months, pairs = build_period_sources(
        history, models, periods)
    assert years == [2026, 2027]
    assert months == [1, 2, 4]
    assert accounts == ["01234", "05678"]
    assert names == ["rf", "svr"]
    assert "svr|2026-04" in pairs
    assert "rf|2026-04" not in pairs
    lookup = {item[0]: item for item in sources[1:] if item[0]}
    assert lookup["svr|2026-02|05678|2026-02"][5] == 0
    assert lookup["svr|2026-04|05678|2026-04"][5] == 18
    assert lookup["svr|2027-01|01234|2027-01"][5] == 175
    assert lookup["rf|2026-02|01234|2026-02"][5] == 160
    ranking = {item[8]: item[9:11] for item in sources if item[8]}
    assert ranking["svr|2026-04|1"] == ["01234", 170]
    assert ranking["rf|2026-02|1"] == ["01234", 160]
    coverage = {item[11]: item[12:14] for item in sources if item[11]}
    assert coverage["svr|2026-04|2026-04"][1] == 2
    assert coverage["rf|2026-02|2026-02"][1] == 1


def test_unavailable_period_listed_but_not_plotted_as_zero():
    rows = [
        _forecast("01234", 2026, 2, "svr", 150),
        _forecast("01234", 2026, 4, "svr", ""),
    ]
    models, periods = collect_model_periods(rows)
    assert periods == [(2026, 2), (2026, 4)]
    assert set(models) == {(2026, 2, "svr")}


class Recorder:
    def __init__(self):
        self.calls = []
    def spreadsheets(self):
        return self
    def values(self):
        return self
    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return self
    def clear(self, **kwargs):
        self.calls.append(("clear", kwargs))
        return self
    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return self
    def batchUpdate(self, **kwargs):
        self.calls.append(("batch", kwargs))
        return self
    def execute(self):
        if self.calls and self.calls[-1][0] == "get":
            return {"sheets": [{"properties": {
                "sheetId": 456, "title": "Datos_Graficos_Cuenta",
                "gridProperties": {"rowCount": 20, "columnCount": 14},
            }}]}
        return {}


def test_google_sheet_has_four_live_dropdowns_and_period_keyed_formulas():
    history, rf, chosen = fixtures()
    rows = [
        _forecast("01234", 2026, 2, "rf", 155),
        _forecast("05678", 2026, 2, "rf", 15),
        _forecast("01234", 2026, 4, "svr", 199),
    ]
    frames, periods = collect_model_periods(rows)
    sources, accounts, models, years, months, pairs = build_period_sources(
        history, frames, periods)
    dashboard_rows, charts, _ = build_dashboard(history, rf, chosen, 123)
    sheet = SheetsService.__new__(SheetsService)
    sheet.api = Recorder()
    sheet.spreadsheet_id = "fake-id"
    sheet.ensure_sheet = lambda name: False
    sheet.sheet_chart_info = lambda name: (123, {})
    result = sheet.replace_dashboard(
        "Graficos_Predicciones", dashboard_rows, charts,
        account_data_rows=sources, accounts=accounts, models=models,
        years=years, months=months, model_period_pairs=pairs,
        account_sheet_name="Datos_Graficos_Cuenta",
    )
    assert result["charts"] == 3
    assert any("'Datos_Graficos_Cuenta'!A:Q" == d["range"]
               for action, d in sheet.api.calls if action == "clear")

    updates = {params["range"]: params["body"]["values"]
               for kind, params in sheet.api.calls if kind == "update"}
    summary = updates["'Graficos_Predicciones'!A2"][0][0]
    for keyword in ("$B$4", "$F$4", "$I$4", "$K$4", "COUNTIF"):
        assert keyword in summary
    assert "DATE($I$4;$K$4;1)" in updates["'Graficos_Predicciones'!A6:A14"][0][0]
    assert "DATE($I$4;$K$4;1)" in updates["'Graficos_Predicciones'!H6:H14"][0][0]
    for selector in ("B6:C14", "E6:F20", "I6:J14"):
        f = updates[f"'Graficos_Predicciones'!{selector}"][0][0]
        assert "DATE($I$4;$K$4;1)" in f

    all_batches = [params["body"] for kind, params in sheet.api.calls
                   if kind == "batch"]
    rules = [x["setDataValidation"]["range"]["startColumnIndex"]
             for body in all_batches for x in body["requests"]
             if "setDataValidation" in x]
    assert set(rules) == {1, 5, 8, 10}  # Cuenta, modelo, año, mes.
    assert dashboard_rows[3][8] == 2026
    assert dashboard_rows[3][10] == 2
    grow = [req["updateSheetProperties"] for kind, params in sheet.api.calls
            if kind == "batch" for req in params["body"]["requests"]
            if "updateSheetProperties" in req and
            req["updateSheetProperties"]["properties"].get("sheetId") == 456]
    assert len(grow) == 1
    assert grow[0]["properties"]["gridProperties"]["columnCount"] == 17
    assert grow[0]["properties"]["gridProperties"]["rowCount"] >= len(sources)


def test_publishing_period_with_only_abstentions_keeps_selector_and_no_fake_value(
        tmp_path, monkeypatch):
    """Si abril tiene 0 pronósticos, no debe fallar la publicación de gráficos."""
    import ml.sheets_dashboard as module
    from types import SimpleNamespace
    history, _, _ = fixtures()
    snap = tmp_path / "history.csv"
    history.to_csv(snap, index=False)
    active = [
        _forecast("01234", 2026, 2, "older", 155),
        _forecast("05678", 2026, 4, "new", ""),
        _forecast("01234", 2026, 4, "new", ""),
    ]

    class Request:
        def execute(self):
            return {"values": [["01234"]]}

    class API:
        def spreadsheets(self):
            return self
        def values(self):
            return self
        def get(self, **kwargs):
            return Request()

    class Sheets:
        last = None
        def __init__(self, *_args):
            self.api = API()
            self.spreadsheet_id = "id"
        def get_rows(self, name, **kwargs):
            assert name == "Predicciones_Cuenta"
            return active
        def sheet_names(self):
            return ["Predicciones_Cuenta"]
        def ensure_sheet(self, name):
            return False
        def sheet_chart_info(self, name):
            return 99, {}
        def replace_dashboard(self, name, rows, charts, **kwargs):
            Sheets.last = (rows, kwargs)
            return {"rows": len(rows), "charts": len(charts)}

    monkeypatch.setattr(module, "get_ml_settings", lambda: SimpleNamespace(
        google_client_secret_file="unused", google_token_file="unused",
        spreadsheet_id="id", sheet_predictions_account="Predicciones_Cuenta",
    ))
    monkeypatch.setattr(module, "SheetsService", Sheets)
    monkeypatch.setattr(module, "latest_snapshot", lambda: {"file": str(snap)})
    summary = module.dashboard(write=True, year=2026, month=4)
    rows, metadata = Sheets.last
    assert summary["periodo"] == "2026-04"
    assert summary["cuentas"] == 0
    assert rows[3][8] == 2026 and rows[3][10] == 4
    assert rows[3][5] == "new"
    assert metadata["months"] == [2, 4]
    assert "new" in metadata["models"]
    assert not metadata["model_period_pairs"] or all(
        "new|2026-04" != x for x in metadata["model_period_pairs"])
