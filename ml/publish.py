"""Publica el pronóstico por cuenta en su propia hoja, sin tocar `Predicciones`.

`Predicciones` tiene contrato de total por sede y no admite un pronóstico por
cuenta. Esta hoja nueva (`Predicciones_Cuenta`) conserva la cuenta como unidad y
la sede solo como trazabilidad. No se escribe nada sin `--write`.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import joblib
import pandas as pd

from app.config import get_ml_settings
from ml.dataset import DATASET_VERSION, normalize_columns, prediction_features
from ml.registry import champion_model_path
from ml.snapshot import latest_snapshot
from services.sheets import SheetsService

HEADERS = ["Fecha ejecución", "Sede", "Cuenta", "Año", "Mes",
           "Consumo predicho (kWh)", "Modelo", "Antigüedad (meses)", "Política"]


def _months(frame: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(frame["Año"], errors="coerce") * 12 + pd.to_numeric(frame["Mes"], errors="coerce")


def default_target(history: pd.DataFrame) -> tuple[int, int]:
    """Primer mes sin ninguna observación: pronóstico puro, igual que en producción."""
    raw = normalize_columns(history.copy())
    months = _months(raw).dropna()
    if months.empty:
        raise RuntimeError("El snapshot no tiene periodos válidos.")
    year, month = divmod(int(months.max()) + 1, 12)
    return year, month


def model_id(model_path) -> str:
    parent = Path(model_path).parent.name
    return "champion" if parent in {"", ".", "champion", "candidates"} else parent


def build_predictions(history: pd.DataFrame, model, identifier: str, year: int, month: int):
    """Una fila por cuenta con pronóstico para el mes pedido, sin usar datos futuros."""
    spec = getattr(model, "ecodata_spec_", None)
    if not spec or spec.get("dataset_version") != DATASET_VERSION:
        raise RuntimeError("Modelo incompatible con la preparación temporal v1; reentrena.")

    raw = normalize_columns(history.copy())
    raw["Cuenta"] = raw["Cuenta"].astype(str)
    target = year * 12 + month
    rows, summary = [], {"publicadas": 0, "observadas": 0, "abstenciones": 0, "sin_entrenamiento": 0}

    for cuenta, group in raw.groupby("Cuenta", sort=True):
        months = _months(group)
        if (months == target).any():
            summary["observadas"] += 1
            continue
        if cuenta not in spec["trained_accounts"]:
            summary["sin_entrenamiento"] += 1
            continue
        try:
            features = prediction_features(group.to_dict("records"), cuenta, year, month,
                                           spec["gap_policy"], spec["include_flagged"], spec["quality_rules"])
        except RuntimeError:
            summary["abstenciones"] += 1
            continue

        value = max(0.0, float(model.predict(features[spec["features"]])[0]))
        latest = group.loc[months.idxmax()]
        rows.append([date.today().isoformat(), str(latest.get("Sede", "")).strip(), cuenta, year, month,
                     round(value, 3), identifier, int(features["history_age"].iloc[0]), spec["gap_policy"]])
        summary["publicadas"] += 1

    rows.sort(key=lambda row: (row[1], row[2]))
    return pd.DataFrame(rows, columns=HEADERS), summary


def _period_key(value) -> str:
    try:
        return str(int(float(str(value).strip())))
    except (TypeError, ValueError):
        return str(value or "").strip()


def prediction_key(row: dict) -> tuple[str, str, str, str]:
    """Identidad de una fila publicada: cuenta, año, mes y modelo."""
    return (
        str(row.get("Cuenta") or "").strip(),
        _period_key(row.get("Año")),
        _period_key(row.get("Mes")),
        str(row.get("Modelo") or "").strip(),
    )


def new_rows(predictions: pd.DataFrame, existing: list[dict]) -> pd.DataFrame:
    """Solo las filas que aún no están en la hoja; reejecutar no duplica."""
    seen = {prediction_key(row) for row in existing}
    keep = [prediction_key(row) for row in predictions.to_dict("records")]
    return predictions.loc[[index not in seen for index in keep]].reset_index(drop=True)


def publish(year: int | None = None, month: int | None = None, model_path=None, write: bool = False):
    snapshot = latest_snapshot()
    if not snapshot:
        raise RuntimeError("Sin snapshot local. Ejecuta ml.snapshot antes de publicar.")
    history = pd.read_csv(snapshot["file"], dtype=str, keep_default_na=False)

    resolved = Path(model_path) if model_path else champion_model_path()
    if resolved is None or not resolved.exists():
        raise RuntimeError("Sin modelo validado. Indica --model-path o promueve uno elegible.")

    if year is None or month is None:
        year, month = default_target(history)

    predictions, summary = build_predictions(history, joblib.load(resolved), model_id(resolved), year, month)
    print(f"Pronóstico {year}-{month:02d} | modelo={resolved}")
    print(" | ".join(f"{key}={value}" for key, value in summary.items()))
    if predictions.empty:
        print("Sin cuentas publicables para ese mes.")
    else:
        print(predictions.to_string(index=False))

    if write:
        settings = get_ml_settings()
        sheets = SheetsService(settings.google_client_secret_file, settings.google_token_file, settings.spreadsheet_id)
        created = sheets.ensure_sheet(settings.sheet_predictions_account)
        existing = sheets.get_rows(settings.sheet_predictions_account)
        fresh = new_rows(predictions, existing)
        sheets.append_rows(settings.sheet_predictions_account, fresh.values.tolist(), headers=HEADERS)
        action = "creada" if created else "actualizada"
        print(f"Hoja '{settings.sheet_predictions_account}' {action}: {len(fresh)} filas nuevas, "
              f"{len(predictions) - len(fresh)} ya presentes.")
    else:
        print("Modo lectura: agrega --write para publicar en la hoja.")
    return predictions, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Pronóstico por cuenta a un mes y publicación en hoja propia.")
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--model-path")
    parser.add_argument("--write", action="store_true", help="Agregar las filas a Predicciones_Cuenta")
    args = parser.parse_args()
    if (args.year is None) != (args.month is None):
        parser.error("Indica --year y --month juntos, o ninguno para usar el mes siguiente")
    publish(args.year, args.month, args.model_path, args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
