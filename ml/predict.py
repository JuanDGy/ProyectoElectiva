"""Predicción desacoplada: calcula rezagos desde el histórico y usa el champion."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from ml.dataset import normalize_columns
from ml.registry import champion_model_path
from ml.snapshot import latest_snapshot
from services.sheets import SheetsService

PREDICTION_HEADERS = [
    "Fecha ejecucion", "Sede", "Año", "Mes",
    "Consumo predicho (kWh)", "Modelo",
]


def compute_lags(history: pd.DataFrame, sede: str) -> dict:
    df = normalize_columns(history)
    sede_df = df[df["Sede"] == sede].copy()
    if sede_df.empty:
        raise RuntimeError(f"Sin histórico para la sede {sede}.")
    sede_df["Año"] = pd.to_numeric(sede_df["Año"], errors="coerce")
    sede_df["Mes"] = pd.to_numeric(sede_df["Mes"], errors="coerce")
    sede_df["Consumo"] = pd.to_numeric(sede_df["Consumo"], errors="coerce")
    sede_df = sede_df.dropna(subset=["Año", "Mes", "Consumo"]).sort_values(["Año", "Mes"])
    if len(sede_df) < 2:
        raise RuntimeError(f"Histórico insuficiente para {sede}: {len(sede_df)} filas.")
    last = sede_df["Consumo"].iloc[-3:].tolist()
    return {
        "lag_1": float(last[-1]),
        "lag_2": float(last[-2]),
        "rolling_3": float(sum(last) / len(last)),
    }


def predict(
    sede: str,
    year: int,
    month: int,
    model_path: str | None = None,
    data_csv: str | None = None,
    log: bool = True,
    lags: dict | None = None,
) -> float:
    resolved_model = model_path or (
        str(champion_model_path()) if champion_model_path() else "models/consumo_random_forest.joblib"
    )
    if not Path(resolved_model).exists():
        raise RuntimeError(
            f"Modelo no encontrado: {resolved_model}. Entrena primero con ml.train."
        )
    if lags is None:
        if data_csv:
            history = pd.read_csv(data_csv)
        else:
            snap = latest_snapshot()
            if snap is None:
                raise RuntimeError("Sin snapshot de datos; indica --data o genera uno con ml.snapshot.")
            history = pd.read_csv(snap["file"])
        lags = compute_lags(history, sede)

    model = joblib.load(resolved_model)
    x = pd.DataFrame([{
        "Mes": month, "Año": year, "Sede": sede,
        "lag_1": lags["lag_1"], "lag_2": lags["lag_2"], "rolling_3": lags["rolling_3"],
    }])
    value = float(model.predict(x)[0])

    if log:
        from app.config import get_ml_settings

        settings = get_ml_settings()
        sheets = SheetsService(
            settings.google_client_secret_file,
            settings.google_token_file,
            settings.spreadsheet_id,
        )
        sheets.append(
            settings.sheet_predictions,
            [datetime.now().isoformat(timespec="seconds"), sede, year, month, value, Path(resolved_model).name],
            headers=PREDICTION_HEADERS,
        )

    print(f"Predicción consumo [{sede} {year}-{month:02d}]: {value:.3f} kWh ({Path(resolved_model).name})")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Predecir consumo con el champion.")
    parser.add_argument("--sede", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--data", default=None, help="CSV histórico para calcular rezagos.")
    parser.add_argument("--no-log", action="store_true", help="No escribir en Sheets.")
    parser.add_argument("--lag-1", type=float, default=None)
    parser.add_argument("--lag-2", type=float, default=None)
    parser.add_argument("--rolling-3", type=float, default=None)
    args = parser.parse_args()

    lags = None
    if args.lag_1 is not None and args.lag_2 is not None and args.rolling_3 is not None:
        lags = {"lag_1": args.lag_1, "lag_2": args.lag_2, "rolling_3": args.rolling_3}
    predict(args.sede, args.year, args.month, args.model_path, args.data,
            log=not args.no_log, lags=lags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
