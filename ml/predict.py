from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from app.config import get_settings
from services.sheets import SheetsService


def predict(
    model_path: str,
    sede: str,
    year: int,
    month: int,
    lag_1: float,
    lag_2: float,
    rolling_3: float,
):
    model = joblib.load(model_path)

    x = pd.DataFrame([{
        "Mes": month,
        "Año": year,
        "Sede": sede,
        "lag_1": lag_1,
        "lag_2": lag_2,
        "rolling_3": rolling_3,
    }])

    value = float(model.predict(x)[0])

    settings = get_settings()
    sheets = SheetsService(
        settings.google_client_secret_file,
        settings.google_token_file,
        settings.spreadsheet_id,
    )
    sheets.append(settings.sheet_predictions, [
        datetime.now().isoformat(timespec="seconds"),
        sede,
        year,
        month,
        value,
        Path(model_path).name,
    ])

    print(f"Predicción consumo: {value:.3f}")
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model-path", required=True)
    p.add_argument("--sede", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--lag-1", type=float, required=True)
    p.add_argument("--lag-2", type=float, required=True)
    p.add_argument("--rolling-3", type=float, required=True)
    a = p.parse_args()

    predict(
        a.model_path,
        a.sede,
        a.year,
        a.month,
        a.lag_1,
        a.lag_2,
        a.rolling_3,
    )
