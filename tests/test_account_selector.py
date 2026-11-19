"""Verifica el origen del selector dinámico y la preservación de cuentas."""
import pandas as pd

from ml.sheets_dashboard import (
    build_account_sources, build_dashboard, select_forecasts,
)


def fixtures():
    rows = []
    for account, levels in (("01234", (120, 150)), ("05678", (8, 12))):
        for month, value in zip((11, 12), levels):
            rows.append({"Oficina": "Sede A", "Cuenta": account,
                         "N° Factura": f"{account}-2025-{month}",
                         "Año": 2025, "Mes": month, "kWh": value})
    history = pd.DataFrame(rows)
    predictions, period = select_forecasts([
        {"Cuenta": "01234", "Año": 2026, "Mes": 2,
         "Modelo": "rf", "Consumo predicho (kWh)": 155},
        {"Cuenta": "05678", "Año": 2026, "Mes": 2,
         "Modelo": "rf", "Consumo predicho (kWh)": 15},
    ])
    return history, predictions, period


def test_selector_preserves_leading_zero_and_forecast_per_account():
    history, forecasts, period = fixtures()
    source = build_account_sources(history, forecasts, period)
    assert source[0][:3] == ["Clave", "Cuenta", "Mes"]
    assert len(source) == 1 + 9 * 2
    options = [row[5] for row in source[1:3]]
    assert options == ["01234", "05678"]
    records = {row[0]: row for row in source[1:]}
    assert records["01234|2025-12"][3:5] == [150, 150]
    assert records["01234|2026-01"][3:5] == ["SIN_DATO", "SIN_DATO"]
    assert records["01234|2026-02"][3:5] == ["SIN_DATO", 155.0]
    assert records["05678|2025-12"][3:5] == [12, 12]
    assert records["05678|2026-02"][3:5] == ["SIN_DATO", 15.0]


def test_dashboard_respects_selected_account_and_explains_dropdown():
    history, forecasts, period = fixtures()
    rows, charts, result = build_dashboard(
        history, forecasts, period, 99, selected_account="05678")
    assert rows[3][1] == "05678"
    assert "desplegable" in rows[3][2]
    assert result["cuenta_destacada"] == "05678"
    assert rows[11][1] == 12.0
    assert rows[13][2] == 15.0
    assert len(charts) == 3


def test_dashboard_ignores_stale_selection():
    history, forecasts, period = fixtures()
    rows, charts, result = build_dashboard(
        history, forecasts, period, 99, selected_account="inexistente")
    assert rows[3][1] in ("01234", "05678")
    assert result["cuentas"] == 2
