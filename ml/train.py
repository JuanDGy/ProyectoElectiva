"""Entrenamiento con tracking MLflow + registro champion/challenger."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.config import get_ml_settings
from ml.dataset import (
    FEATURES,
    TARGET,
    build_features,
    normalize_columns,
    regression_metrics,
    temporal_split,
    validate_dataset,
)
from ml.registry import CANDIDATES_DIR, add_challenger, get_champion, promote
from ml.snapshot import latest_snapshot, take_snapshot
from services.sheets import SheetsService


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _build_estimator(model_type: str):
    if model_type == "linear":
        return LinearRegression()
    if model_type == "random_forest":
        return RandomForestRegressor(n_estimators=300, random_state=42, n_jobs=-1)
    raise ValueError("model_type debe ser 'linear' o 'random_forest'")


def train(
    model_type: str = "random_forest",
    data_csv: str | None = None,
    experiment: str = "ecodata-consumo",
    n_test_periods: int = 2,
) -> dict:
    import mlflow

    settings = None
    if data_csv:
        rows = pd.read_csv(data_csv).to_dict(orient="records")
        data_md5, data_source = None, data_csv
    else:
        settings = get_ml_settings()
        snap = latest_snapshot()
        if snap is None:
            sheets = SheetsService(
                settings.google_client_secret_file,
                settings.google_token_file,
                settings.spreadsheet_id,
            )
            snap = take_snapshot(sheets.get_rows(settings.sheet_bd_energy))
        rows = pd.read_csv(snap["file"]).to_dict(orient="records")
        data_md5, data_source = snap["md5"], snap["file"]

    warnings = validate_dataset(normalize_columns(pd.DataFrame(rows)))
    for warning in warnings:
        print(f"Advertencia de datos: {warning}")

    df = build_features(rows)
    train_df, test_df = temporal_split(df, n_test_periods)

    numeric = ["Mes", "Año", "lag_1", "lag_2", "rolling_3"]
    pre = ColumnTransformer([
        ("num", Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]), numeric),
        ("cat", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), ["Sede"]),
    ])
    pipeline = Pipeline([("preprocess", pre), ("model", _build_estimator(model_type))])
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    pred = pipeline.predict(test_df[FEATURES])
    metrics = regression_metrics(test_df[TARGET], pred)

    baseline_per_sede = {
        sede: {
            "mean_consumo": float(g["Consumo"].mean()),
            "n": int(len(g)),
            "train_max_period": str(g["periodo"].max().date()),
        }
        for sede, g in train_df.groupby("Sede")
    }

    run_id = f"{model_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    candidate_dir = CANDIDATES_DIR / run_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    model_path = candidate_dir / "model.joblib"
    joblib.dump(pipeline, model_path)

    # Compatibilidad: artefacto único sobrescrito como antes.
    Path("models").mkdir(exist_ok=True)
    joblib.dump(pipeline, Path("models") / f"consumo_{model_type}.joblib")

    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_id):
        mlflow.log_params({
            "model_type": model_type,
            "features": ",".join(FEATURES),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "n_test_periods": n_test_periods,
        })
        mlflow.log_metrics({k: v for k, v in metrics.items() if v is not None})
        mlflow.set_tags({
            "data_snapshot": data_source,
            "data_md5": data_md5 or "adhoc-csv",
            "git_sha": _git_sha(),
        })
        mlflow.log_artifact(str(model_path))

    test_periods = sorted(str(p.date()) for p in test_df["periodo"].unique())
    entry = {
        "id": run_id,
        "model_type": model_type,
        "model_path": model_path.as_posix(),
        "features": FEATURES,
        "metrics": metrics,
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "test_periods": test_periods,
        "data_snapshot": data_source,
        "data_md5": data_md5,
        "baseline_per_sede": baseline_per_sede,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
    }
    (candidate_dir / "metadata.json").write_text(
        json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    add_challenger(entry)

    if get_champion() is None:
        promoted, reason = promote(run_id, metric="mape")
        print(f"Auto-promoción a champion: {reason}")
    else:
        champion = get_champion()
        print(f"Champion vigente: {champion['id']} (mape={champion['metrics'].get('mape')})")
        print(f"Evalúa promoción con: uv run python -m ml.promote --candidate {run_id}")

    print(f"Modelo: {model_path}")
    print(f"Métricas (test {test_periods}): {metrics}")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrenar modelo de consumo con tracking.")
    parser.add_argument("--model", choices=["linear", "random_forest"], default="random_forest")
    parser.add_argument("--data", default=None, help="CSV de snapshot; por defecto el último (o Sheets).")
    parser.add_argument("--experiment", default="ecodata-consumo")
    parser.add_argument("--n-test-periods", type=int, default=2)
    args = parser.parse_args()
    train(args.model, args.data, args.experiment, args.n_test_periods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
