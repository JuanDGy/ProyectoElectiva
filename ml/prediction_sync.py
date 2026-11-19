"""Publicación sin pérdida de evidencia: upsert de previsiones con historial.

La hoja principal conserva una fila vigente por Cuenta+Año+Mes. Antes de
reemplazarla, almacena la versión previa en Historial_Predicciones.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math

import pandas as pd

from ml.publish import HEADERS, _period_key

EXTRA_HEADERS = ["Snapshot SHA-256", "Estado modelo"]
CURRENT_HEADERS = HEADERS + EXTRA_HEADERS
HISTORY_HEADERS = CURRENT_HEADERS + ["Archivado el", "Motivo reemplazo", "ID de cambio"]
MAIN_SHEET = "Predicciones_Cuenta"
HISTORY_SHEET = "Historial_Predicciones"


def active_key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("Cuenta") or "").strip(),
            _period_key(row.get("Año")), _period_key(row.get("Mes")))


def _number(value):
    if value is None or str(value).strip() == "":
        return None
    val = str(value).replace(" ", "")
    if "," in val:
        val = val.replace(".", "").replace(",", ".")
    try:
        return float(val)
    except ValueError:
        return None


def _equivalent(previous: dict, new: dict) -> bool:
    # La fecha de ejecución no es el criterio de identidad.
    for col in CURRENT_HEADERS:
        if col == "Fecha ejecución":
            continue
        old, actual = previous.get(col, ""), new.get(col, "")
        if col == "Consumo predicho (kWh)":
            a, b = _number(old), _number(actual)
            if a is None or b is None or not math.isclose(a, b, abs_tol=.0005):
                return False
        elif str(old).strip() != str(actual).strip():
            return False
    return True


def plan_sync(predictions: pd.DataFrame, existing: list[dict],
              snapshot_hash: str, state: str) -> dict:
    if predictions.empty:
        return {"updates": [], "adds": [], "archive": [], "unchanged": 0}
    missing = set(HEADERS) - set(predictions.columns)
    if missing:
        raise ValueError(f"Columnas ausentes: {sorted(missing)}")
    enriched = predictions.copy()
    enriched[EXTRA_HEADERS[0]] = snapshot_hash
    enriched[EXTRA_HEADERS[1]] = state

    known = {}
    for index, item in enumerate(existing, start=2):
        if not item.get("Cuenta") or not item.get("Año") or not item.get("Mes"):
            continue
        key = active_key(item)
        if key in known:
            raise ValueError(f"Hay dos filas vigentes con la misma cuenta/periodo: {key}. "
                             "Conciliar manualmente antes de sincronizar.")
        known[key] = (index, item)

    updates, additions, archive, unchanged = [], [], [], 0
    input_seen = set()
    for row in enriched.to_dict("records"):
        key = active_key(row)
        if key in input_seen:
            raise ValueError(f"Predicción repetida en el lote: {key}")
        input_seen.add(key)
        target = [row.get(col, "") for col in CURRENT_HEADERS]
        if key not in known:
            additions.append(target)
            continue
        row_number, previous = known[key]
        if _equivalent(previous, row):
            unchanged += 1
            continue
        old = [previous.get(col, "") for col in CURRENT_HEADERS]
        reason = (f"Modelo {previous.get('Modelo', '')} -> {row.get('Modelo', '')}; "
                  "nuevo cálculo/snapshot")
        revision_id = hashlib.sha256(json.dumps({
            "clave": key, "anterior": old,
            "nueva": [v for i, v in enumerate(target) if i != 0],
        }, ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()
        archive.append([*old, datetime.now(timezone.utc).isoformat(),
                        reason, revision_id])
        updates.append((row_number, target))
    return {"updates": updates, "adds": additions, "archive": archive,
            "unchanged": unchanged}


def sync_predictions(sheets, sheet_name: str, predictions: pd.DataFrame,
                     snapshot_hash: str, state: str):
    """Historial primero, actualización después. No toca cuentas ajenas al lote."""
    if sheet_name != MAIN_SHEET:
        raise ValueError("Upsert solo en la pestaña reservada Predicciones_Cuenta")
    sheets.ensure_sheet(sheet_name)
    existing = sheets.get_rows(sheet_name, unformatted=True)
    plan = plan_sync(predictions, existing, snapshot_hash, state)
    if not any(plan[key] for key in ("updates", "adds")):
        sheets.format_predictions_sheet(sheet_name, CURRENT_HEADERS)
        return {k: len(v) if isinstance(v, list) else v for k, v in plan.items()}

    sheets.ensure_headers(sheet_name, CURRENT_HEADERS)
    archived_now = 0
    if plan["archive"]:
        sheets.ensure_sheet(HISTORY_SHEET)
        # Idempotencia también ante interrupción entre archivar y reemplazar.
        earlier = sheets.get_rows(HISTORY_SHEET, unformatted=True)
        archived_ids = {x.get("ID de cambio") for x in earlier
                        if x.get("ID de cambio")}
        fresh_archive = [row for row in plan["archive"]
                         if row[-1] not in archived_ids]
        if fresh_archive:
            # La auditoría es obligatoria ANTES de sobrescribir.
            sheets.append_rows(HISTORY_SHEET, fresh_archive,
                               headers=HISTORY_HEADERS)
            archived_now = len(fresh_archive)
        sheets.format_predictions_history(HISTORY_SHEET)

    # Google Sheets no ofrece transacción atómica entre ambas pestañas.
    # Todas las filas modificadas se envían en un batch de valores.
    if plan["updates"]:
        payload = [{"range": f"'{sheet_name}'!A{n}:K{n}", "values": [row]}
                   for n, row in plan["updates"]]
        sheets.api.spreadsheets().values().batchUpdate(
            spreadsheetId=sheets.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": payload},
        ).execute()
    if plan["adds"]:
        sheets.append_rows(sheet_name, plan["adds"], headers=CURRENT_HEADERS)
    sheets.format_predictions_sheet(sheet_name, CURRENT_HEADERS)
    summary = {k: len(v) if isinstance(v, list) else v for k, v in plan.items()}
    summary["archive"] = archived_now
    return summary
