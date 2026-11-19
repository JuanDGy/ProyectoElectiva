"""Reporte visual del modelo: series con pronóstico, errores, cobertura y comparación.

Genera PNG + un `index.html` autocontenido en `reports/report_FECHA/`. Es de solo
lectura: no reentrena, no promueve y no escribe en Sheets.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ml import publish
from ml.dataset import DATASET_VERSION, normalize_columns
from ml.snapshot import latest_snapshot

CANDIDATES = Path("models/candidates")
REPORTS = Path("reports")
COLORS = {"real": "#1f3a93", "test": "#e67e22", "futuro": "#c0392b", "persistencia": "#7f8c8d"}


def _style(ax, title, ylabel="kWh"):
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=8)
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.tick_params(labelsize=7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _read_json(path: Path) -> dict:
    """Los JSON del proyecto pueden venir en UTF-8 o cp1252 según la corrida."""
    for encoding in ("utf-8", "cp1252"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def latest_candidate(root: Path = CANDIDATES) -> Path:
    dirs = [p for p in root.glob("*") if (p / "model.joblib").exists()]
    if not dirs:
        raise RuntimeError("Sin candidatos entrenados en models/candidates.")
    return max(dirs, key=lambda p: (p / "model.joblib").stat().st_mtime)


def chart_comparison(out: Path) -> pd.DataFrame:
    files = sorted(REPORTS.glob("comparison_*.json"))
    if not files:
        return pd.DataFrame()
    data = _read_json(files[-1])
    rows = []
    for item in data["candidates"]:
        rows.append({
            "modelo": item["id"],
            "tipo": item["model_type"],
            "mae": item["metrics"]["mae"],
            "wape": item["metrics"]["wape"],
            "eligible": item.get("promotion_eligible", False),
        })
    baseline = data["candidates"][0]["baseline_metrics"]["persistence"]["mae"]
    frame = pd.DataFrame(rows).sort_values("mae")
    if frame.empty:
        return frame

    fig, ax = plt.subplots(figsize=(max(7, 1.6 * len(frame) + 2), 3.4))
    labels = [f"{r.tipo}\n{r.modelo.rsplit('_', 1)[-1]}" for r in frame.itertuples()]
    bars = ax.bar(range(len(frame)), frame.mae,
                  color=["#27ae60" if e else "#95a5a6" for e in frame.eligible])
    ax.axhline(baseline, color=COLORS["persistencia"], linestyle="--", linewidth=1.2,
               label=f"Persistencia = {baseline:,.2f}")
    ax.set_xticks(range(len(frame)))
    ax.set_xticklabels(labels, fontsize=6, rotation=30, ha="right")
    for bar, value in zip(bars, frame.mae):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:,.0f}",
                ha="center", va="bottom", fontsize=6)
    ax.legend(fontsize=7, frameon=False)
    _style(ax, "MAE en prueba común (menor es mejor)", "MAE (kWh)")
    fig.tight_layout()
    fig.savefig(out / "modelo_vs_persistencia.png", dpi=150)
    plt.close(fig)
    return frame


def chart_errors(predictions: pd.DataFrame, out: Path) -> dict:
    frame = predictions.copy()
    frame["residual"] = frame.prediction - frame.target_consumo
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.2))

    explicit = frame.residual.dropna()
    axes[0].hist(explicit, bins=30, color="#2c3e50", alpha=0.85)
    axes[0].axvline(0, color="#c0392b", linewidth=1)
    axes[0].set_xlabel("Predicción − real (kWh)", fontsize=8)
    _style(axes[0], "Distribución del error", "Filas")

    top = frame.reindex(frame.residual.abs().sort_values(ascending=False).index).head(12)
    axes[1].scatter(top.target_consumo, top.prediction, s=28, color="#c0392b", alpha=0.8)
    limit = float(max(top.target_consumo.max(), top.prediction.max(), 1))
    axes[1].plot([0, limit], [0, limit], color="#7f8c8d", linewidth=1, linestyle="--")
    axes[1].set_xlabel("Real (kWh)", fontsize=8)
    axes[1].set_ylabel("Predicho (kWh)", fontsize=8)
    _style(axes[1], "Los 12 errores más grandes", "")
    fig.tight_layout()
    fig.savefig(out / "errores.png", dpi=150)
    plt.close(fig)
    return {"mae": float(explicit.abs().mean()), "n": int(explicit.size),
            "p90": float(explicit.abs().quantile(0.9))}


def chart_coverage(candidate: Path, out: Path) -> dict | None:
    path = candidate / "quality_report.json"
    test_path = candidate / "test.csv"
    if not path.exists() or not test_path.exists():
        return None
    quality = _read_json(path)
    test = pd.read_csv(test_path)
    counts = test.groupby(pd.to_datetime(test.periodo)).size()

    fig, ax = plt.subplots(figsize=(6.4, 3))
    ax.bar(counts.index.strftime("%Y-%m"), counts.values, color="#2980b9", width=0.5)
    for x, value in enumerate(counts.values):
        ax.text(x, value, str(value), ha="center", va="bottom", fontsize=6)
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(counts.index.strftime("%Y-%m"), fontsize=7)
    _style(ax, "Filas evaluadas por mes (prueba)", "Filas")
    fig.tight_layout()
    fig.savefig(out / "cobertura.png", dpi=150)
    plt.close(fig)
    return quality


def chart_series(history: pd.DataFrame, predictions: pd.DataFrame, forecast: pd.DataFrame,
                 out: Path, top: int = 12) -> list[str]:
    raw = normalize_columns(history.copy())
    raw["Cuenta"] = raw["Cuenta"].astype(str)
    raw["periodo"] = pd.to_datetime(raw["periodo"], errors="coerce")
    raw["Consumo"] = pd.to_numeric(raw["Consumo"], errors="coerce")
    raw = raw.dropna(subset=["periodo", "Consumo"])

    ranking = raw.groupby("Cuenta").Consumo.agg(["sum", "size"])
    chosen = ranking[ranking["size"] >= 6].sort_values("sum", ascending=False).head(top).index

    fig, axes = plt.subplots(3, 4, figsize=(12, 6.4), sharex=False)
    pred = predictions.copy()
    pred["periodo"] = pd.to_datetime(pred.periodo)
    fut = forecast.copy()

    for ax, cuenta in zip(axes.ravel(), chosen):
        series = raw[raw.Cuenta == cuenta].sort_values("periodo")
        ax.plot(series.periodo, series.Consumo, marker="o", markersize=2.5,
                linewidth=1, color=COLORS["real"], label="Real")
        points = pred[(pred.Cuenta.astype(str) == cuenta) & pred.target_consumo.notna()]
        if not points.empty:
            ax.scatter(points.periodo, points.prediction, s=22, color=COLORS["test"],
                       zorder=3, label="Predicción (test)")
        future = fut[fut.Cuenta.astype(str) == cuenta]
        if not future.empty:
            last = series.iloc[-1]
            ax.scatter(future.periodo, future.prediction, s=45, marker="*",
                       color=COLORS["futuro"], zorder=3, label="Pronóstico")
            ax.plot([last.periodo, future.periodo.iloc[0]],
                    [last.Consumo, future.prediction.iloc[0]],
                    linestyle=":", linewidth=0.9, color=COLORS["futuro"])
        sede = str(series.Sede.iloc[-1] or "").strip()
        label = f"{cuenta} · {sede}" if len(sede) > 3 else cuenta
        ax.set_title(label, fontsize=8)
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.25, linewidth=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for label in ax.get_xticklabels():
            label.set_rotation(30)

    for ax in axes.ravel()[len(chosen):]:
        ax.axis("off")
    handles, labels = axes.ravel()[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=8, frameon=False,
               bbox_to_anchor=(0.5, 0.002))
    fig.suptitle("Serie por cuenta: histórico, prueba y pronóstico", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    fig.savefig(out / "series_cuentas.png", dpi=150)
    plt.close(fig)
    return list(chosen)


def _table(frame: pd.DataFrame, columns: list[str], limit: int = 30) -> str:
    if frame.empty:
        return "<p>Sin filas.</p>"
    view = frame.head(limit)[columns].copy()
    for column in columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "")
    return view.to_html(index=False, border=0, classes="tabla", escape=True)


def build(candidate: Path | None = None, year: int | None = None,
          month: int | None = None, model_path: str | None = None) -> Path:
    candidate = Path(candidate) if candidate else latest_candidate()
    out = REPORTS / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    history = pd.read_csv(candidate / "accepted_history.csv", dtype={"Cuenta": str},
                          keep_default_na=False)
    predictions = pd.read_csv(candidate / "test_predictions.csv", dtype={"Cuenta": str})
    comparison = chart_comparison(out)
    errors = chart_errors(predictions, out)
    quality = chart_coverage(candidate, out)

    resolved = Path(model_path) if model_path else candidate / "model.joblib"
    model = joblib.load(resolved)
    snapshot = latest_snapshot()
    if snapshot is None:
        raise RuntimeError("Sin snapshot local; ejecuta ml.snapshot.")
    source = pd.read_csv(snapshot["file"], dtype=str, keep_default_na=False)
    if year is None or month is None:
        year, month = publish.default_target(source)
    forecast, summary = publish.build_predictions(source, model, publish.model_id(resolved), year, month)
    if not forecast.empty:
        forecast = forecast.rename(columns={"Consumo predicho (kWh)": "prediction"})
        forecast = forecast.assign(periodo=pd.to_datetime(dict(
            year=forecast["Año"], month=forecast["Mes"], day=1)))
    chart_series(history, predictions, forecast, out)

    spec = model.ecodata_spec_
    metrics = {"MAE prueba": float(predictions.assign(r=lambda d: (d.prediction - d.target_consumo).abs()).r.mean()),
               "MAE persistencia": float(predictions.assign(r=lambda d: (d.persistence - d.target_consumo).abs()).r.mean()),
               "MAE media móvil": float(predictions.assign(r=lambda d: (d.rolling_mean - d.target_consumo).abs()).r.mean()),
               "filas de prueba": int(len(predictions))}

    html = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>Reporte {candidate.name}</title>",
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#222;max-width:1100px}"
        "h1{font-size:22px}h2{font-size:16px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}"
        ".kpi{display:inline-block;margin:0 24px 8px 0}.kpi b{font-size:20px;display:block}"
        ".tabla{border-collapse:collapse;font-size:11px}.tabla th{background:#eef2f7;text-align:left;padding:4px 8px}"
        ".tabla td{padding:3px 8px;border-bottom:1px solid #eee}img{max-width:100%;height:auto}"
        ".nota{font-size:12px;color:#555}</style>",
        f"<h1>Reporte de consumo · {candidate.name}</h1>",
        f"<p class='nota'>Generado {datetime.now():%Y-%m-%d %H:%M} · dataset {spec.get('dataset_version')} "
        f"· política {spec.get('gap_policy')} · pronóstico {year}-{month:02d}</p>",
        "<p class='kpi'><b>{:,.2f}</b>MAE del modelo</p>"
        "<p class='kpi'><b>{:,.2f}</b>MAE persistencia</p>"
        "<p class='kpi'><b>{:,.2f}</b>MAE media móvil</p>"
        "<p class='kpi'><b>{}</b>filas de prueba</p>".format(
            metrics["MAE prueba"], metrics["MAE persistencia"],
            metrics["MAE media móvil"], metrics["filas de prueba"]),
        "<h2>Modelos frente a la persistencia</h2>"
        "<img src='modelo_vs_persistencia.png'>"
        "<p class='nota'>Verde: mejora el MAE de prueba y cumple la condición de promoción. "
        "Gris: no la cumple. La comparación usa la prueba común; no es un ranking de producción.</p>",
        "<h2>Errores</h2><img src='errores.png'>"
        f"<p class='nota'>MAE explícito {errors['mae']:,.2f} kWh sobre {errors['n']} filas · "
        f"P90 del error {errors['p90']:,.2f} kWh.</p>",
    ]
    if quality:
        html.append(
            "<h2>Cobertura</h2><img src='cobertura.png'>"
            f"<p class='nota'>Cobertura de prueba {quality['common_test_coverage_pct']:.1f}% · "
            f"cobertura con la política del modelo {quality['policy_prediction_coverage_pct']:.1f}% "
            f"({quality['policy_test_predictions']} predicciones, {quality['policy_abstentions']} abstenciones).</p>")
    html += [
        "<h2>Serie por cuenta</h2><img src='series_cuentas.png'>",
        f"<h2>Pronóstico {year}-{month:02d}</h2>",
        "<p class='nota'>" + " · ".join(f"{k}={v}" for k, v in summary.items()) + "</p>",
        _table(forecast, ["Sede", "Cuenta", "prediction", "Antigüedad (meses)"]),
        "<h2>Predicciones de prueba</h2>",
        _table(predictions.assign(error=lambda d: (d.prediction - d.target_consumo).round(2))
               .sort_values("error", key=lambda s: s.abs(), ascending=False),
               ["Cuenta", "Sede", "periodo", "target_consumo", "prediction", "error"]),
    ]
    if not comparison.empty:
        html += ["<h2>Métricas por candidato</h2>", _table(comparison, ["modelo", "tipo", "mae", "wape"])]

    (out / "index.html").write_text("\n".join(html), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Reporte visual del modelo (PNG + HTML).")
    parser.add_argument("--candidate", help="Carpeta del candidato; por defecto el más reciente")
    parser.add_argument("--model-path", help="Modelo a usar para el pronóstico futuro")
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    args = parser.parse_args()
    if (args.year is None) != (args.month is None):
        parser.error("Indica --year y --month juntos")
    out = build(args.candidate, args.year, args.month, args.model_path)
    print(f"Reporte: {out / 'index.html'}")
    for image in sorted(out.glob("*.png")):
        print(f"  {image.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
