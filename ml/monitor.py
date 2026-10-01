"""Monitoreo: drift de consumo por sede, cobertura y vigencia del champion.

Salida: reports/monitor_<timestamp>.json. Exit 0 = OK, 2 = drift o problema.
Política: revisión bimestral; ante drift se dispara reentrenamiento (ml.train
+ ml.promote) comparando champion vs candidato antes de desplegar.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ml.dataset import normalize_columns
from ml.registry import get_champion
from ml.snapshot import latest_snapshot

REPORTS_DIR = Path("reports")
DRIFT_THRESHOLD_PCT = 25.0
STALE_DAYS = 60


def check(
    history_csv: str,
    drift_threshold_pct: float = DRIFT_THRESHOLD_PCT,
) -> dict:
    champion = get_champion()
    if champion is None:
        raise RuntimeError("Sin champion registrado; entrena y promueve un modelo primero.")

    df = normalize_columns(pd.read_csv(history_csv))
    df["Año"] = pd.to_numeric(df["Año"], errors="coerce")
    df["Mes"] = pd.to_numeric(df["Mes"], errors="coerce")
    df["Consumo"] = pd.to_numeric(df["Consumo"], errors="coerce")
    df = df.dropna(subset=["Sede", "Año", "Mes", "Consumo"]).sort_values(["Sede", "Año", "Mes"])

    baseline = champion.get("baseline_per_sede", {})
    drifted, missing_baseline, coverage_gaps = [], [], []
    for sede, g in df.groupby("Sede"):
        recent = g["Consumo"].iloc[-2:]
        recent_mean = float(recent.mean())
        base = baseline.get(str(sede))
        if base is None:
            missing_baseline.append(str(sede))
            continue
        change_pct = (
            abs(recent_mean - base["mean_consumo"]) / base["mean_consumo"] * 100
            if base["mean_consumo"] else 0.0
        )
        if change_pct >= drift_threshold_pct:
            drifted.append({
                "sede": str(sede),
                "baseline_mean": round(base["mean_consumo"], 2),
                "recent_mean": round(recent_mean, 2),
                "change_pct": round(change_pct, 2),
            })
        # Cobertura: meses faltantes entre min y max del histórico de la sede.
        first = g.sort_values(["Año", "Mes"]).iloc[[0, -1]]
        expected = pd.period_range(
            f"{int(first['Año'].iloc[0])}-{int(first['Mes'].iloc[0]):02d}",
            f"{int(first['Año'].iloc[1])}-{int(first['Mes'].iloc[1]):02d}",
            freq="M",
        )
        actual = pd.to_datetime(
            dict(year=g["Año"].astype(int), month=g["Mes"].astype(int), day=1),
            errors="coerce",
        ).dt.to_period("M").dropna().unique()
        missing_months = len(expected) - len(actual)
        if missing_months > 0:
            coverage_gaps.append({"sede": str(sede), "missing_months": int(missing_months)})

    champion_age_days = (
        datetime.now() - datetime.fromisoformat(champion["timestamp"])
    ).days
    stale = champion_age_days > STALE_DAYS

    needs_retrain = bool(drifted) or stale
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "champion": champion["id"],
        "champion_age_days": champion_age_days,
        "champion_stale": stale,
        "drift_threshold_pct": drift_threshold_pct,
        "drifted_sedes": drifted,
        "sedes_without_baseline": missing_baseline,
        "coverage_gaps": coverage_gaps,
        "needs_retrain": needs_retrain,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitorear drift y vigencia del modelo.")
    parser.add_argument("--data", default=None)
    parser.add_argument("--drift-threshold", type=float, default=DRIFT_THRESHOLD_PCT)
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR))
    args = parser.parse_args()

    if args.data:
        history_csv = args.data
    else:
        snap = latest_snapshot()
        if snap is None:
            raise RuntimeError("Sin snapshot de datos; genera uno con ml.snapshot.")
        history_csv = snap["file"]

    report = check(history_csv, args.drift_threshold)
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Reporte: {out}")
    if report["needs_retrain"]:
        print("ACCIÓN: reentrenar (uv run python -m ml.train) y comparar con ml.promote.")
        return 2
    print("OK: sin drift; champion vigente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
