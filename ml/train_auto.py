"""Entrenamiento operativo multialgoritmo: selección por CV, no por test.

Reutiliza un reporte de competencia DE LA MISMA versión del histórico y entrena
un artefacto de inferencia por familia. Una mejora no demostrada frente a
persistencia NO se convierte en champion.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

import joblib
import numpy as np
import pandas as pd

from ml.dataset import DATASET_VERSION, TARGET, build_features, load_quality_rules
from ml.forecast_estimator import ForecastEstimator
from ml.registry import CANDIDATES_DIR, add_challenger, promote, get_champion
from ml.snapshot import latest_snapshot
from scripts.experiment_models import GRID, _make, _features

SELECTION_PATH = Path("models/auto_selection.json")
DEFAULT_REPORTS_DIR = Path("reports")



def _history():
    snap = latest_snapshot()
    if not snap:
        raise RuntimeError("Sin snapshot: ejecuta uv run python -m ml.snapshot.")
    path = Path(snap["file"])
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if hashlib.md5(content).hexdigest() != snap["md5"]:
        raise RuntimeError("Snapshot inconsistente con el manifiesto")
    return path, checksum, pd.read_csv(path, dtype=str, keep_default_na=False)


def _report_for_snapshot(path: Path, sha256: str, report_path: Path | None = None):
    def valid(candidate):
        record = json.loads(candidate.read_text(encoding="utf-8"))
        if Path(record.get("source", "")).resolve() != path.resolve():
            return None
        if record.get("source_sha256") and record["source_sha256"] != sha256:
            return None
        # Reportes antiguos no guardaban hash: no aceptar un CSV modificado
        # después del experimento. El snapshot es inmutable por convención.
        if not record.get("source_sha256") and path.stat().st_mtime > candidate.stat().st_mtime:
            return None
        if not record.get("results") or not record.get("baselines"):
            return None
        return record
    if report_path:
        record = valid(Path(report_path))
        if record is None:
            raise RuntimeError("Reporte incompatible con snapshot/hash actuales")
        return Path(report_path), record
    for candidate in sorted(DEFAULT_REPORTS_DIR.glob("experiment_models_*.json"),
                            key=lambda f: f.stat().st_mtime, reverse=True):
        record = valid(candidate)
        if record:
            return candidate, record
    raise RuntimeError(
        "No existe una competencia correspondiente al snapshot actual. "
        "Ejecuta primero: uv run python cli.py competencia")


def choose_configurations(report: dict, families: set[str]) -> dict:
    """Un representante POR FAMILIA según MAE de validación temporal."""
    chosen = {}
    for item in report["results"]:
        family = item["model"]
        if family not in families:
            continue
        mae = float(item.get("validation_mae", float("inf")))
        if not np.isfinite(mae):
            continue
        order = (mae, str(item["approach"]), int(bool(item["seasonal"])),
                 json.dumps(item.get("params", {}), sort_keys=True))
        if family not in chosen or order < chosen[family][0]:
            chosen[family] = (order, item)
    missing = families - set(chosen)
    if missing:
        raise RuntimeError("Faltan evaluaciones en el reporte para: "+", ".join(sorted(missing)))
    return {key: item for key, (_, item) in chosen.items()}


def fit_selected(frame: pd.DataFrame, rule_set: dict, choice: dict):
    seasonal = bool(choice["seasonal"])
    features = _features(seasonal)
    data = frame[frame.periodo.notna()].copy()
    if len(data) < 12:
        raise RuntimeError("No hay suficientes filas para reentrenar.")
    estimator = _make(choice["model"], seasonal, **choice.get("params", {}))
    mode = choice["approach"]
    if mode == "level":
        target = data[TARGET]
    elif mode == "delta":
        target = data[TARGET] - data["lag_1"]
    elif mode == "ratio":
        target = data[TARGET] / (data["lag_1"] + 1.0)
    else:
        raise ValueError(f"Enfoque desconocido: {mode}")
    estimator.fit(data[features], target)
    spec = {
        "dataset_version": DATASET_VERSION, "features": features,
        "trained_accounts": sorted(data.Cuenta.astype(str).unique().tolist()),
        "gap_policy": "causal", "include_flagged": False,
        "quality_rules": rule_set,
        "approach": mode, "model_family": choice["model"],
        "selection_method": "min_validation_mae",
        "train_target_end": data.periodo.max().strftime("%Y-%m"),
        "prediction_protocol": "one_month_causal",
        "negative_predictions": "clip_zero",
    }
    return ForecastEstimator(estimator, mode, features, spec), len(data)


def train_selected(models: str = "all", report: Path | None = None,
                   allow_promote: bool = True, evaluate: bool = False):
    source, sha256, history = _history()
    all_families = {model for model, _ in GRID}
    chosen_families = all_families if models == "all" else {
        name.strip() for name in models.split(",") if name.strip()}
    if not chosen_families or chosen_families - all_families:
        raise ValueError("Modelos disponibles: " + ", ".join(sorted(all_families)))
    if evaluate:
        # Evaluación sobre snapshot actual. Su salida no afecta las hojas.
        subprocess.run(
            [sys.executable, "-m", "scripts.experiment_models", "--models", models],
            check=True)
    file, experiment = _report_for_snapshot(source, sha256, report)
    selections = choose_configurations(experiment, chosen_families)
    # IMPORTANTE: No elegir por MAE de test. La prueba sirve solo para
    # rechazar una promoción cuando no hay evidencia de mejora.
    winner = min(selections.values(), key=lambda e: (
        float(e["validation_mae"]), e["model"], e["approach"]))
    report_n = int(experiment["test_rows"])
    rules = load_quality_rules()
    feature_frame = build_features(history.to_dict("records"), "strict", False, rules)
    baseline = experiment["baselines"]
    floor = min(float(x["mae"]) for x in baseline.values())
    results = []
    for family, choice in sorted(selections.items()):
        print(f"Entrenando {family} ({choice['approach']}) ...", flush=True)
        model, count = fit_selected(feature_frame, rules, choice)
        tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        ident = f"auto_{family}_{tag}_{uuid.uuid4().hex[:6]}"
        destination = CANDIDATES_DIR / ident
        destination.mkdir(parents=True, exist_ok=False)
        path = destination / "model.joblib"
        joblib.dump(model, path)
        t = choice["test"]
        # Promoción solo si el modelo GANADOR por validación supera ambos
        # baselines y tiene evidencia robusta del bootstrap de prueba.
        eligible = (t["mae"] < floor and
                    float(choice.get("vs_persistence", {}).get("p_better", 0)) >= .95)
        entry = {
            "id": ident, "model_path": path.as_posix(), "model_type": family,
            "approach": choice["approach"], "dataset_version": DATASET_VERSION,
            "data_snapshot": source.as_posix(), "data_sha256": sha256,
            "validation_metrics": {"mae": float(choice["validation_mae"])},
            "metrics": t, "baseline_metrics": baseline,
            "promotion_eligible": eligible,
            "evaluation_id": hashlib.sha256(json.dumps({
                "source_sha256": sha256, "test_rows": report_n,
                "test_start": experiment.get("test_start", "2025-11-01"),
                "test_end": experiment.get("test_end", "2025-12-01"),
                "baselines": baseline,
                "validation_periods": experiment.get("validation_periods"),
            }, sort_keys=True).encode()).hexdigest(),
            "report": file.as_posix(), "train_rows": count,
            "test_rows": report_n, "parameters": choice.get("params", {}),
            "seasonal": bool(choice["seasonal"]),
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "fit_scope": "all_observed_after_holdout_evaluation",
            "report_test_metrics_are_pre_refit": True,
            "confidence_notice": "Experimental hasta pasar controles de promoción.",
        }
        (destination / "metadata.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        add_challenger(entry)
        results.append(entry)
    winner_entry = next(x for x in results if
                        x["model_type"] == winner["model"])
    # selection metadata is explicit, even if candidate cannot be promoted.
    promoted, reason = False, "Promoción automática desactivada."
    if allow_promote and winner_entry["promotion_eligible"]:
        promoted, reason = promote(winner_entry["id"], metric="mae")
    elif allow_promote:
        reason = ("Sin promoción: el ganador por validación NO demostró mejora "
                  "suficiente frente a persistencia y media móvil.")
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "selected_candidate": winner_entry["id"],
        "selected_model_path": winner_entry["model_path"],
        "selected_family": winner["model"],
        "selected_approach": winner["approach"],
        "validation_mae": float(winner["validation_mae"]),
        "test_mae": float(winner["test"]["mae"]),
        "baseline_mae": float(baseline["persistence"]["mae"]),
        "champion_promoted": promoted, "promotion_reason": reason,
        "report": file.as_posix(), "snapshot_sha256": sha256,
        "candidates": [x["id"] for x in results],
        "status": "Champion" if promoted else "Seleccionado por CV (experimental)",
    }
    SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTION_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    DEFAULT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    Path("reports/auto_selection_latest.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Seleccionado por validación:", winner_entry["id"])
    print("MAE validación:", round(summary["validation_mae"], 2),
          "| MAE test:", round(summary["test_mae"], 2),
          "| Persistencia test:", round(summary["baseline_mae"], 2))
    print(reason)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Entrenar familias de la competencia y elegir por validación.")
    p.add_argument("--modelos", default="all", help="all o familias separadas por coma")
    p.add_argument("--seleccion", default="automatica", choices=("automatica",))
    p.add_argument("--reporte", type=Path, help="Reporte de competencia del mismo snapshot")
    p.add_argument("--evaluar", action="store_true", help="Ejecutar competencia nueva antes de entrenar")
    p.add_argument("--sin-promover", action="store_true",
                   help="Entrenar y seleccionar, sin intentar promover Champion")
    args = p.parse_args(argv)
    train_selected(args.modelos, args.reporte, not args.sin_promover, args.evaluar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
