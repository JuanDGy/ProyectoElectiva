"""Construcción y validación del dataset de consumo (puro, sin E/S)."""
from __future__ import annotations

import math

import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

FEATURES = ["Mes", "Año", "Sede", "lag_1", "lag_2", "rolling_3"]
TARGET = "target_consumo"
REQUIRED = {"Sede", "Año", "Mes", "Consumo"}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
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
    return df.rename(columns=rename)


def validate_dataset(df: pd.DataFrame) -> list[str]:
    """Valida el esquema normalizado (Sede, Año, Mes, Consumo).

    Devuelve advertencias de calidad (lista vacía = sin observaciones).
    """
    warnings = []
    missing = REQUIRED - set(df.columns)
    if missing:
        raise RuntimeError(
            "Faltan columnas para entrenamiento: " + ", ".join(sorted(missing))
        )
    if df.empty:
        raise RuntimeError("Dataset vacío: sin filas para entrenamiento.")
    nulls = df[list(REQUIRED)].isna().sum()
    for col, n in nulls.items():
        if n:
            warnings.append(f"Columna {col}: {n} valores nulos (se descartan).")
    dupes = df.duplicated(subset=["Sede", "Año", "Mes"]).sum()
    if dupes:
        warnings.append(
            f"{dupes} filas duplicadas por (Sede, Año, Mes): se conserva la última."
        )
    per_sede = df.groupby("Sede").size()
    thin = per_sede[per_sede < 4].index.tolist()
    if thin:
        warnings.append(
            f"Sedes con menos de 4 registros (lags poco fiables): {', '.join(map(str, thin))}."
        )
    return warnings


def build_features(rows: list[dict]) -> pd.DataFrame:
    """Normaliza filas crudas y crea rezagos + objetivo (consumo siguiente)."""
    df = normalize_columns(pd.DataFrame(rows))
    validate_dataset(df)

    df["Año"] = pd.to_numeric(df["Año"], errors="coerce")
    df["Mes"] = pd.to_numeric(df["Mes"], errors="coerce")
    df["Consumo"] = pd.to_numeric(df["Consumo"], errors="coerce")
    df = df.dropna(subset=["Sede", "Año", "Mes", "Consumo"])
    df = df.drop_duplicates(subset=["Sede", "Año", "Mes"], keep="last")

    df["periodo"] = pd.to_datetime(
        dict(year=df["Año"].astype(int), month=df["Mes"].astype(int), day=1),
        errors="coerce",
    )
    df = df.dropna(subset=["periodo"]).sort_values(["Sede", "periodo"])

    group = df.groupby(["Sede"], group_keys=False)
    df["lag_1"] = group["Consumo"].shift(1)
    df["lag_2"] = group["Consumo"].shift(2)
    df["rolling_3"] = group["Consumo"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )
    df["target_consumo"] = group["Consumo"].shift(-1)
    return df.dropna(subset=["target_consumo", "lag_1", "lag_2", "rolling_3"]).reset_index(drop=True)


def temporal_split(
    df: pd.DataFrame, n_test_periods: int = 2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Test = últimos N periodos globales (evaluación fuera de tiempo real)."""
    periods = sorted(df["periodo"].unique())
    if len(periods) < n_test_periods + 1:
        raise RuntimeError(
            f"Periodos insuficientes para split temporal: {len(periods)} "
            f"(se necesitan al menos {n_test_periods + 1})."
        )
    cutoff = periods[-n_test_periods]
    train_df = df[df["periodo"] < cutoff].reset_index(drop=True)
    test_df = df[df["periodo"] >= cutoff].reset_index(drop=True)
    if len(train_df) < 12:
        raise RuntimeError(
            f"Entrenamiento insuficiente tras el split: {len(train_df)} filas."
        )
    if test_df.empty:
        raise RuntimeError("El split temporal dejó el conjunto de prueba vacío.")
    return train_df, test_df


def regression_metrics(y_true, y_pred) -> dict:
    y = pd.Series(y_true).to_numpy(dtype=float)
    p = pd.Series(y_pred).to_numpy(dtype=float)
    rmse = float(math.sqrt(mean_squared_error(y, p)))
    r2 = float(r2_score(y, p)) if len(y) >= 2 else None
    if r2 is not None and not math.isfinite(r2):
        r2 = None
    mask = y != 0
    mape = float((abs((y[mask] - p[mask]) / y[mask])).mean() * 100) if mask.any() else None
    return {
        "mae": float(mean_absolute_error(y, p)),
        "rmse": rmse,
        "r2": r2,
        "mape": mape,
    }
