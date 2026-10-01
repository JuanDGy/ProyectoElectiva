from __future__ import annotations

from pathlib import Path
import argparse
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.config import get_settings
from services.sheets import SheetsService


def _to_dataframe(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    rename = {}
    for col in df.columns:
        n = col.lower().replace("°", "").replace(" ", "").replace("_", "")
        if "oficina" in n or n == "sede":
            rename[col] = "Sede"
        elif n in {"ano", "año"}:
            rename[col] = "Año"
        elif n == "mes":
            rename[col] = "Mes"
        elif "kwh" in n:
            rename[col] = "Consumo"
        elif "valor" in n and ("total" in n or "factur" in n):
            rename[col] = "ValorFacturado"

    df = df.rename(columns=rename)
    return df


def build_dataset(sheets: SheetsService, settings) -> pd.DataFrame:
    df = _to_dataframe(sheets.get_rows(settings.sheet_bd_energy))

    needed = {"Sede", "Año", "Mes", "Consumo"}
    missing = needed - set(df.columns)
    if missing:
        raise RuntimeError(
            "Faltan columnas para entrenamiento: "
            + ", ".join(sorted(missing))
        )

    df["Año"] = pd.to_numeric(df["Año"], errors="coerce")
    df["Mes"] = pd.to_numeric(df["Mes"], errors="coerce")
    df["Consumo"] = pd.to_numeric(df["Consumo"], errors="coerce")
    df = df.dropna(subset=["Sede", "Año", "Mes", "Consumo"])

    df["periodo"] = pd.to_datetime(
        dict(year=df["Año"].astype(int), month=df["Mes"].astype(int), day=1),
        errors="coerce",
    )
    df = df.sort_values(["Sede", "periodo"])

    group = df.groupby(["Sede"], group_keys=False)
    df["lag_1"] = group["Consumo"].shift(1)
    df["lag_2"] = group["Consumo"].shift(2)
    df["rolling_3"] = group["Consumo"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )

    # Target: consumo del siguiente periodo.
    df["target_consumo"] = group["Consumo"].shift(-1)
    return df.dropna(subset=["target_consumo"])


def train(model_type: str = "random_forest"):
    settings = get_settings()
    sheets = SheetsService(
        settings.google_client_secret_file,
        settings.google_token_file,
        settings.spreadsheet_id,
    )
    df = build_dataset(sheets, settings)

    if len(df) < 12:
        raise RuntimeError(
            f"Dataset insuficiente para una prueba razonable: {len(df)} filas utilizables."
        )

    features = ["Mes", "Año", "Sede", "lag_1", "lag_2", "rolling_3"]
    target = "target_consumo"

    # Split temporal: último 20% para evaluación.
    split = max(1, int(len(df) * 0.8))
    train_df = df.iloc[:split]
    test_df = df.iloc[split:]

    numeric = ["Mes", "Año", "lag_1", "lag_2", "rolling_3"]
    categorical = ["Sede"]

    pre = ColumnTransformer([
        (
            "num",
            Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]),
            numeric,
        ),
        (
            "cat",
            Pipeline([
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("onehot", OneHotEncoder(handle_unknown="ignore")),
            ]),
            categorical,
        ),
    ])

    if model_type == "linear":
        estimator = LinearRegression()
    elif model_type == "random_forest":
        estimator = RandomForestRegressor(
            n_estimators=300,
            random_state=42,
            n_jobs=-1,
        )
    else:
        raise ValueError("model_type debe ser 'linear' o 'random_forest'")

    pipeline = Pipeline([
        ("preprocess", pre),
        ("model", estimator),
    ])

    pipeline.fit(train_df[features], train_df[target])

    metrics = {}
    if not test_df.empty:
        pred = pipeline.predict(test_df[features])
        y = test_df[target].to_numpy()
        metrics = {
            "mae": float(mean_absolute_error(y, pred)),
            "rmse": float(np.sqrt(mean_squared_error(y, pred))),
            "r2": float(r2_score(y, pred)) if len(y) >= 2 else None,
        }

    Path("models").mkdir(exist_ok=True)
    model_path = Path("models") / f"consumo_{model_type}.joblib"
    meta_path = Path("models") / f"consumo_{model_type}.json"

    joblib.dump(pipeline, model_path)
    meta_path.write_text(
        json.dumps({
            "model_type": model_type,
            "features": features,
            "rows": len(df),
            "metrics": metrics,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("Modelo:", model_path)
    print("Métricas:", metrics)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=["linear", "random_forest"],
        default="random_forest",
    )
    args = parser.parse_args()
    train(args.model)
