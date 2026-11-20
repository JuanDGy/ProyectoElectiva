"""Gráficos nativos de Google Sheets para los pronósticos por cuenta.

La pestaña Graficos_Predicciones es administrada por esta herramienta. Conserva
Predicciones y Predicciones_Cuenta intactas; sincroniza los mismos gráficos por
título en lugar de crear duplicados. Sin --write no modifica la hoja.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from app.config import get_ml_settings
from ml.dataset import normalize_columns, prepare_history
from ml.snapshot import latest_snapshot
from services.sheets import SheetsService

DASHBOARD_SHEET = "Graficos_Predicciones"
PREDICTION_SHEET = "Predicciones_Cuenta"
ACCOUNT_DATA_SHEET = "Datos_Graficos_Cuenta"


def _chart(sheet_id: int, title: str, chart_type: str, domain: int,
           series: list[int], n: int, anchor: int) -> dict:
    def rng(col):
        return {"sources": [{
            "sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": max(6, n + 5),
            "startColumnIndex": col, "endColumnIndex": col + 1,
        }]}
    labels = "Cuenta" if chart_type == "BAR" else "Mes"
    units = "Cuentas" if domain == 7 else "kWh"
    source_series = []
    for index, col in enumerate(series):
        item = {"series": {"sourceRange": rng(col)},
                "targetAxis": "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS"}
        if chart_type == "LINE":
            item["lineStyle"] = {
                "type": "SOLID" if index == 0 else "MEDIUM_DASHED",
                "width": 3 if index else 2,
            }
            item["pointStyle"] = {"shape": "CIRCLE" if index == 0 else "DIAMOND",
                                  "size": 6 if index == 0 else 10}
            item["colorStyle"] = {"rgbColor": (
                {"red": .13, "green": .43, "blue": .70} if index == 0
                else {"red": .94, "green": .44, "blue": .15}
            )}
        source_series.append(item)
    basic = {
        "chartType": chart_type, "legendPosition": "BOTTOM_LEGEND",
        "headerCount": 1,
        "axis": [
            {"position": "LEFT_AXIS" if chart_type == "BAR" else "BOTTOM_AXIS",
             "title": labels},
            {"position": "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS",
             "title": units},
        ],
        "domains": [{"domain": {"sourceRange": rng(domain)}}],
        "series": source_series,
    }
    if chart_type == "LINE":
        # Une el último dato REAL con el PRONÓSTICO, aun cuando hay meses sin PDF.
        # No introduce estimaciones para los meses faltantes: las celdas siguen vacías.
        basic["interpolateNulls"] = True
    return {"anchor": anchor, "spec": {"title": title, "basicChart": basic}}


def select_forecasts(rows: list[dict], model: str | None = None,
                     year: int | None = None, month: int | None = None) -> tuple[pd.DataFrame, tuple]:
    """Elige un único modelo/mes, evitando sumar pronósticos alternativos."""
    if not rows:
        raise RuntimeError("Predicciones_Cuenta no contiene pronósticos; primero publica uno.")
    data = pd.DataFrame(rows)
    needed = {"Cuenta", "Año", "Mes", "Modelo", "Consumo predicho (kWh)"}
    if not needed.issubset(data):
        raise RuntimeError("La pestaña Predicciones_Cuenta tiene columnas incompatibles.")
    data["Año"] = pd.to_numeric(data["Año"], errors="coerce")
    data["Mes"] = pd.to_numeric(data["Mes"], errors="coerce")
    # Google Sheets puede entregar decimales con coma según configuración regional.
    def parse_number(value):
        txt = str(value if value is not None else "").strip()  # 0 kWh también es válido
        if "," in txt and "." in txt:
            txt = (txt.replace(".", "").replace(",", ".")
                   if txt.rfind(",") > txt.rfind(".") else txt.replace(",", ""))
        elif "," in txt:
            txt = txt.replace(",", ".")
        try:
            return float(txt)
        except ValueError:
            return float("nan")
    data["Consumo predicho (kWh)"] = data["Consumo predicho (kWh)"].map(parse_number)
    data = data.dropna(subset=["Año", "Mes", "Consumo predicho (kWh)"])
    if year is not None and month is not None:
        data = data[(data["Año"] == year) & (data["Mes"] == month)]
    if model:
        data = data[data["Modelo"] == model]
    if data.empty:
        raise RuntimeError("No hay pronósticos para el modelo y periodo indicados.")
    # Modelo y mes escogidos se muestran explícitamente; no mezclar cohortes.
    periods = data[["Año", "Mes"]].drop_duplicates().itertuples(index=False, name=None)
    chosen_period = max((int(y), int(m)) for y, m in periods)
    data = data[(data["Año"] == chosen_period[0]) & (data["Mes"] == chosen_period[1])]
    if not model:
        model = sorted(Counter(data["Modelo"]).items(), key=lambda x: (-x[1], x[0]))[0][0]
        data = data[data["Modelo"] == model]
    data = data.drop_duplicates("Cuenta", keep="last").copy()
    data["Cuenta"] = data["Cuenta"].astype(str).str.strip()
    data = data.sort_values("Consumo predicho (kWh)", ascending=False)
    return data, (*chosen_period, model)


def build_dashboard(history: pd.DataFrame, predictions: pd.DataFrame,
                    chosen: tuple, sheet_id: int = 0,
                    selected_account: str | None = None):
    """Construye datos y especificaciones de tres gráficos nativos de Sheets."""
    year, month, model = chosen
    if predictions.empty:
        raise RuntimeError("No hay predicciones para graficar.")
    clean, _, quality = prepare_history(normalize_columns(history).to_dict("records"))
    eligible = set(predictions["Cuenta"].astype(str))
    clean = clean[clean["Cuenta"].isin(eligible)].copy()
    target = pd.Timestamp(year=year, month=month, day=1)
    start = target - pd.DateOffset(months=8)
    # La cuenta con MAYOR pronóstico puede tener un histórico demasiado vacío
    # para trazar una línea útil. Elegir la de mayor pronóstico ENTRE las que
    # cuentan con al menos 5 observaciones en ventana y dato hace <=2 meses.
    def sample_for(account: str) -> pd.DataFrame:
        return clean[(clean["Cuenta"].eq(account)) &
                     (clean["periodo"].ge(start)) &
                     (clean["periodo"].lt(target))].sort_values("periodo")

    primary = None
    for account in predictions["Cuenta"].astype(str):
        sample = sample_for(account)
        if len(sample) >= 5 and (
                (target.year - sample.iloc[-1]["periodo"].year) * 12 +
                target.month - sample.iloc[-1]["periodo"].month) <= 2:
            primary = account
            break
    if primary is None:
        # Ventana escasa: escoger la cuenta con más meses comprobables y
        # conservar la nota de insuficiencia en la hoja, nunca inventar datos.
        primary = max(predictions["Cuenta"].astype(str),
                      key=lambda account: (len(sample_for(account)),
                                           sample_for(account)["periodo"].max()
                                           if len(sample_for(account)) else pd.Timestamp.min))
    if selected_account and selected_account in set(predictions["Cuenta"].astype(str)):
        primary = selected_account
    primary_history = sample_for(primary).set_index("periodo")["Consumo"]
    last_real_period = primary_history.index.max() if len(primary_history) else None
    last_real_kwh = (float(primary_history.loc[last_real_period])
                     if last_real_period is not None else None)
    estimated = float(predictions.loc[predictions["Cuenta"].astype(str).eq(primary),
                                      "Consumo predicho (kWh)"].iloc[0])
    missing_gap = ((target.year - last_real_period.year) * 12 + target.month -
                   last_real_period.month - 1) if last_real_period is not None else None
    periods = pd.date_range(start=start, end=target, freq="MS")
    series = []
    for period in periods:
        actual = primary_history.get(period)
        real = float(actual) if pd.notna(actual) else ""
        # Dos puntos en la serie discontinua: último consumo REAL y pronóstico.
        # En caso contrario Google Sheets dibuja un punto solitario y no una línea.
        forecast = (round(estimated, 3) if period == target else
                    last_real_kwh if period == last_real_period else "")
        series.append([period.strftime("%Y-%m"), real, forecast])

    ranked = [[str(row["Cuenta"]), round(float(row["Consumo predicho (kWh)"]), 2)]
              for _, row in predictions.head(15).iterrows()]
    # Cobertura, no suma: evita presentar históricos incompletos como totales de sede.
    coverage = []
    for period in periods:
        n = int(clean[clean.periodo.eq(period)]["Cuenta"].nunique())
        forecast_count = len(predictions) if period == target else ""
        coverage.append([period.strftime("%Y-%m"), n, forecast_count])
    length = max(len(series), len(ranked), len(coverage))
    rows = [
        ["EcoData ECCI | Dashboard de predicciones"],
        [f"Periodo estimado: {year}-{month:02d} | Modelo: {model} | Cuentas con pronóstico: {len(predictions)}"],
        ["Datos históricos = facturas; tramo punteado = enlace del último dato real a una estimación. Huecos sin factura NO son consumos observados. Pronóstico experimental."],
        ["Cuenta:", primary,
         "Usa los desplegables: cuenta B4, modelo F4, año I4 y mes K4. Los gráficos cambian solos.",
         "", "Modelo:", model, "", "Año:", year, "Mes:", month],
        ["Mes", "Consumo real (kWh)", "Enlace a estimación (kWh)", "",
         "Cuenta", "Pronóstico kWh", "", "Mes", "Cuentas con dato real", "Cuentas pronosticadas"],
    ]
    for i in range(length):
        s = series[i] if i < len(series) else ["", "", ""]
        r = ranked[i] if i < len(ranked) else ["", ""]
        c = coverage[i] if i < len(coverage) else ["", "", ""]
        rows.append([*s, "", *r, "", *c])
    charts = [
        _chart(sheet_id, "EcoData | Histórico y pronóstico de cuenta destacada", "LINE", 0, [1, 2],
               len(series), 23),
        _chart(sheet_id, "EcoData | Top 15 cuentas pronosticadas", "BAR", 4, [5],
               len(ranked), 51),
        _chart(sheet_id, "EcoData | Cobertura de datos y pronósticos", "COLUMN", 7, [8, 9],
               len(coverage), 79),
    ]
    return rows, charts, {"periodo": f"{year}-{month:02d}", "modelo": model,
                          "cuentas": len(predictions), "cuenta_destacada": primary,
                          "ultimo_mes_real": (last_real_period.strftime("%Y-%m")
                                              if last_real_period is not None else None),
                          "meses_sin_factura_previa": missing_gap,
                          "puntos_reales_grafico": len(primary_history),
                          "filas_historicas_validas": len(clean),
                          "exclusiones_fuente": quality["excluded_rows"]}



def build_account_sources(history: pd.DataFrame, predictions: pd.DataFrame,
                          chosen: tuple) -> list[list]:
    """Tabla auxiliar: una fila por cuenta/mes; sin rellenar los meses sin factura."""
    year, month, _ = chosen
    target = pd.Timestamp(year=year, month=month, day=1)
    periods = pd.date_range(start=target - pd.DateOffset(months=8), end=target, freq="MS")
    clean, _, _ = prepare_history(normalize_columns(history).to_dict("records"))
    clean = clean[clean["Cuenta"].isin(set(predictions["Cuenta"].astype(str)))]
    all_accounts = sorted(predictions["Cuenta"].astype(str).unique())
    result = [["Clave", "Cuenta", "Mes", "Consumo real", "Enlace a estimación",
               "Cuentas del selector"]]
    for account in all_accounts:
        subset = clean[(clean["Cuenta"].eq(account)) &
                       (clean["periodo"].isin(periods))].sort_values("periodo")
        historical = subset.set_index("periodo")["Consumo"]
        prior = historical[historical.index < target]
        latest = prior.index.max() if len(prior) else None
        forecast = float(predictions.loc[
            predictions["Cuenta"].astype(str).eq(account),
            "Consumo predicho (kWh)"].iloc[0])
        for period in periods:
            actual = historical.get(period)
            actual_out = float(actual) if pd.notna(actual) else "SIN_DATO"
            link = (round(forecast, 3) if period == target else
                    float(historical.loc[latest]) if period == latest
                    else "SIN_DATO")
            result.append([f"{account}|{period:%Y-%m}", account,
                           period.strftime("%Y-%m"), actual_out, link, ""])
    # Referencias de validación consecutivas (no una de cada nueve filas).
    for i, account in enumerate(all_accounts):
        result[i + 1][5] = account
    return result



def build_multimodel_sources(history: pd.DataFrame,
                             models: dict[str, pd.DataFrame],
                             period: tuple[int, int]) -> tuple[list[list], list[str], list[str]]:
    """Origen único de los tres gráficos interactivos para todos los modelos.

    A:F histórico + enlace; G/H listas; I:K ranking; L:N cobertura. Todas las
    claves incluyen el MODELO para evitar mezclar predicciones incompatibles.
    """
    year, month = period
    target = f"{year}-{month:02d}"
    model_names = sorted(models)
    account_options = sorted({
        str(account) for frame in models.values() for account in frame["Cuenta"]
    })
    rows = [[
        "Modelo|Cuenta|Mes", "Modelo", "Cuenta", "Mes", "Consumo real",
        "Enlace estimado", "Cuentas (selector)", "Modelos (selector)",
        "Modelo|Posición", "Cuenta top", "kWh top",
        "Modelo|Mes", "Con real", "Con predicción",
    ]]
    rankings, coverage = [], []
    for name, forecasts in sorted(models.items()):
        source = build_account_sources(history, forecasts, (year, month, name))
        count_real_by_month = {}
        for line in source[1:]:
            key, account, m, real, link, _ = line
            rows.append([f"{name}|{key}", name, account, m,
                         real, link, "", "", "", "", "", "", "", ""])
            count_real_by_month[m] = count_real_by_month.get(m, 0) + (
                0 if real == "SIN_DATO" else 1)
        rank = forecasts.sort_values("Consumo predicho (kWh)", ascending=False)
        for index, (_, record) in enumerate(rank.head(15).iterrows(), start=1):
            rankings.append((
                f"{name}|{index}", str(record["Cuenta"]),
                round(float(record["Consumo predicho (kWh)"]), 3)))
        for m in sorted(count_real_by_month):
            coverage.append((
                f"{name}|{m}", count_real_by_month[m],
                len(forecasts) if m == target else "SIN_DATO"))
    if not rows[1:]:
        raise RuntimeError("No hay fuentes de predicción para los modelos seleccionados")
    # Los rangos desplegables requieren opciones contiguas, sin blancos.
    for index, option in enumerate(account_options):
        rows[index+1][6] = option
    for index, name in enumerate(model_names):
        rows[index+1][7] = name
    for index, values in enumerate(rankings):
        rows[index+1][8:11] = list(values)
    for index, values in enumerate(coverage):
        rows[index+1][11:14] = list(values)
    return rows, account_options, model_names


def collect_model_periods(rows: list[dict]) -> tuple[
        dict[tuple[int, int, str], pd.DataFrame], list[tuple[int, int]]]:
    """Índice de cada combinación de año/mes/modelo con predicciones numéricas.

    Los periodos sin pronósticos numéricos también se incluyen como opción
    del selector para mostrar explícitamente que no hay estimación.
    """
    periods, triples = set(), set()
    for item in rows:
        try:
            year, month = int(item.get("Año")), int(item.get("Mes"))
        except (ValueError, TypeError):
            continue
        name = str(item.get("Modelo") or "").strip()
        if not name or not (1 <= month <= 12):
            continue
        periods.add((year, month))
        triples.add((year, month, name))
    frames = {}
    for y, m, name in sorted(triples):
        try:
            frame, _ = select_forecasts(rows, name, y, m)
        except RuntimeError:
            continue
        frames[(y, m, name)] = frame
    return frames, sorted(periods)


def build_period_sources(history: pd.DataFrame,
                         frames: dict[tuple[int, int, str], pd.DataFrame],
                         periods: list[tuple[int, int]]) -> tuple[
                             list[list], list[str], list[str], list[int],
                             list[int], list[str]]:
    """Fuentes de TODOS los periodos, sin cruzar modelos ni fechas.

    A:F historial; G:H selector cuenta/modelo; I:K ranking;
    L:N cobertura; O:P selector año/mes; Q combinaciones válidas.
    """
    if not frames:
        raise RuntimeError("No hay periodos con pronósticos numéricos para graficar")
    accounts = sorted({str(account) for forecast in frames.values()
                       for account in forecast["Cuenta"]})
    models = sorted({name for (_, _, name) in frames})
    years = sorted({y for y, _ in periods})
    months = sorted({m for _, m in periods})
    combinations = sorted({f"{name}|{y}-{m:02d}" for y, m, name in frames})
    result = [[
        "Modelo|Periodo|Cuenta|Mes", "Modelo", "Cuenta", "Mes",
        "Consumo real", "Enlace estimado", "Cuentas (selector)",
        "Modelos (selector)", "Modelo|Periodo|Posición", "Cuenta top",
        "kWh top", "Modelo|Periodo|Mes", "Con real", "Con predicción",
        "Años (selector)", "Meses (selector)", "Modelo|Periodo (válido)",
    ]]
    rankings, coverage = [], []
    for y, m in sorted(periods):
        per_period = {name: frame for (fy, fm, name), frame in frames.items()
                      if (fy, fm) == (y, m)}
        if not per_period:
            continue
        source, _, _ = build_multimodel_sources(history, per_period, (y, m))
        target = f"{y}-{m:02d}"
        for entry in source[1:]:
            model, account, observation = entry[1:4]
            result.append([
                f"{model}|{target}|{account}|{observation}",
                model, account, observation, entry[4], entry[5],
                "", "", "", "", "", "", "", "", "", "", "",
            ])
            if entry[8]:
                # Original: modelo|posición
                rank_model, rank = entry[8].rsplit("|", 1)
                rankings.append([
                    f"{rank_model}|{target}|{rank}", entry[9], entry[10],
                ])
            if entry[11]:
                # Ranking/cobertura tienen su propio modelo de origen;
                # puede ser distinto al de la fila de histórico A:F.
                coverage_model, observed = entry[11].rsplit("|", 1)
                coverage.append([
                    f"{coverage_model}|{target}|{observed}", entry[12], entry[13],
                ])
    max_rows = max(len(result)-1, len(accounts), len(models), len(years),
                   len(months), len(combinations), len(rankings), len(coverage))
    while len(result) <= max_rows:
        result.append([""] * 17)
    for index, value in enumerate(accounts, 1):
        result[index][6] = value
    for index, value in enumerate(models, 1):
        result[index][7] = value
    for index, value in enumerate(rankings, 1):
        result[index][8:11] = value
    for index, value in enumerate(coverage, 1):
        result[index][11:14] = value
    for index, value in enumerate(years, 1):
        result[index][14] = value
    for index, value in enumerate(months, 1):
        result[index][15] = value
    for index, value in enumerate(combinations, 1):
        result[index][16] = value
    return result, accounts, models, years, months, combinations


def dashboard(write: bool = False, model: str | None = None,
              year: int | None = None, month: int | None = None):
    if (year is None) != (month is None):
        raise ValueError("Debes indicar --year y --month juntos.")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("Mes inválido.")
    snapshot = latest_snapshot()
    if snapshot is None:
        raise RuntimeError("No hay snapshot; ejecuta python -m ml.snapshot primero.")
    history = pd.read_csv(Path(snapshot["file"]), dtype=str, keep_default_na=False)
    settings = get_ml_settings()
    sheets = SheetsService(settings.google_client_secret_file, settings.google_token_file,
                           settings.spreadsheet_id)
    active = sheets.get_rows(settings.sheet_predictions_account, unformatted=True)
    historical = (
        sheets.get_rows("Historial_Predicciones", unformatted=True)
        if "Historial_Predicciones" in sheets.sheet_names() else [])
    all_forecasts = active + historical
    # El periodo publicado más reciente tiene prioridad incluso cuando TODAS
    # sus cuentas debieron abstenerse (ej. un mes muy alejado del histórico).
    available_active = []
    for entry in active:
        try:
            ym = (int(entry["Año"]), int(entry["Mes"]))
            if 1 <= ym[1] <= 12:
                available_active.append(ym)
        except (TypeError, ValueError, KeyError):
            continue
    if year is None:
        if not available_active:
            raise RuntimeError("Predicciones_Cuenta no contiene periodos válidos.")
        year, month = max(available_active)
    all_frames, all_periods = collect_model_periods(all_forecasts)
    if not all_frames:
        raise RuntimeError("No hay predicciones numéricas publicadas para construir gráficos.")
    model_frames = {name: frame for (fy, fm, name), frame in all_frames.items()
                    if (fy, fm) == (year, month)}
    if model and not any(str(x.get("Modelo") or "") == model
                         for x in all_forecasts):
        raise RuntimeError(f"Modelo no publicado: {model}")
    period_models = Counter(str(x.get("Modelo") or "") for x in active
                            if str(x.get("Año")) == str(year)
                            and str(x.get("Mes")) == str(month)
                            and x.get("Modelo"))
    selected_model = model or (
        period_models.most_common(1)[0][0] if period_models
        else max(model_frames, key=lambda name: len(model_frames[name]))
        if model_frames else
        max(all_frames, key=lambda key: (key[0], key[1]))[2]
    )
    sheet_id = 0
    current_account = None
    if write:
        sheets.ensure_sheet(DASHBOARD_SHEET)
        sheet_id, _ = sheets.sheet_chart_info(DASHBOARD_SHEET)
        prev = sheets.api.spreadsheets().values().get(
            spreadsheetId=sheets.spreadsheet_id,
            range=f"'{DASHBOARD_SHEET}'!B4").execute().get("values", [])
        if prev and prev[0]:
            current_account = str(prev[0][0]).strip()

    # Un periodo sin números sigue siendo seleccionable, con mensaje visible.
    # Usamos datos de otro periodo solo como plantilla de gráficos; todas
    # las fórmulas consultarán el año/mes REAL de los desplegables.
    has_current_values = selected_model in model_frames
    if has_current_values:
        rendering_period = (year, month, selected_model)
    else:
        alternatives = sorted(
            all_frames,
            key=lambda key: (key[2] == selected_model, key[0], key[1]),
            reverse=True,
        )
        rendering_period = alternatives[0]
    scaffold = all_frames[rendering_period]
    values, charts, summary = build_dashboard(
        history, scaffold, rendering_period, sheet_id, current_account)
    values[3][5] = selected_model
    values[3][8] = year
    values[3][10] = month
    summary["periodo"] = f"{year}-{month:02d}"
    summary["modelo"] = selected_model
    if not has_current_values:
        summary["cuentas"] = 0
        print(f"Sin predicciones numéricas para {year}-{month:02d}; "
              "el selector mostrará el periodo y la ausencia de estimaciones.")
    if write:
        source_rows, all_accounts, all_models, all_years, all_months, pairs = (
            build_period_sources(history, all_frames, all_periods))
        if selected_model not in all_models:
            all_models = sorted([*all_models, selected_model])
            while len(source_rows) <= len(all_models):
                source_rows.append([""] * 17)
            for index, row in enumerate(source_rows[1:]):
                row[7] = all_models[index] if index < len(all_models) else ""
    else:
        source_rows, all_accounts, all_models = None, [], []
        all_years, all_months, pairs = [], [], []
    print(f"Dashboard {summary['periodo']} | modelo={summary['modelo']} | "
          f"cuentas={summary['cuentas']} | destacada={summary['cuenta_destacada']}")
    print(f"Vista previa: {len(values)-5} filas de datos y {len(charts)} gráficos nativos.")
    if write:
        result = sheets.replace_dashboard(
            DASHBOARD_SHEET, values, charts, account_data_rows=source_rows,
            accounts=all_accounts, models=all_models,
            years=all_years, months=all_months, model_period_pairs=pairs,
            account_sheet_name=ACCOUNT_DATA_SHEET)
        print(f"Google Sheets actualizado: {DASHBOARD_SHEET} | "
              f"{result['charts']} gráficos | {result['rows']} filas")
    else:
        print("Solo lectura; agrega --write para crear/actualizar gráficos en Google Sheets.")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gráficos nativos en Google Sheets.")
    parser.add_argument("--write", action="store_true", help="Actualizar la pestaña reservada")
    parser.add_argument("--model", help="Identificador de modelo publicado")
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    args = parser.parse_args(argv)
    dashboard(args.write, args.model, args.year, args.month)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
