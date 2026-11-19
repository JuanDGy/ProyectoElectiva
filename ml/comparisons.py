"""Tablas comparativas auditables y gráficos nativos en Google Sheets.

Se alimenta exclusivamente de reportes experimentales ya existentes. No ejecuta
Gemini, no entrena modelos y no cambia facturas ni pronósticos. Sin --write
genera solamente un resumen local de lo que publicaría.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.config import get_ml_settings
from services.sheets import SheetsService

OCR_SHEET = "Comparativa_OCR_LLM"
MODELS_SHEET = "Comparativa_Modelos"
FIELDS = ("Cuenta", "NumeroFactura", "kWh", "ValorFacturado", "InicioPeriodo", "FinPeriodo")
METHODS = (("ocr", "OCR local"), ("llm", "Gemini (LLM)"),
           ("llm_guard", "Gemini + reglas"))
FAMILY_NAMES = {
    "mean": "Dummy (media)", "linear": "Regresión lineal",
    "ridge": "Ridge", "huber": "Huber", "rf": "Random Forest",
    "rf_mae": "Random Forest (MAE)", "et": "Extra Trees",
    "gbr": "Gradient Boosting", "hgb": "HistGradientBoosting",
    "rbf": "Red radial RBF", "mlp": "Red neuronal MLP",
    "svr": "SVR radial", "lstm": "LSTM",
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_report(directory: Path, pattern: str, accept) -> Path:
    candidates = [p for p in directory.glob(pattern)
                  if p.is_file() and accept(_read(p))]
    if not candidates:
        raise RuntimeError(f"No existe un reporte compatible ({pattern}) en {directory}")
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def default_reports(root: Path = Path("reports")) -> dict[str, Path | None]:
    """No elegir el smoke test ni mezclar una corrida parcial de 5 modelos."""
    ocr = _pick_report(root, "ocr_vs_llm_*.json", lambda r: (
        r.get("resumen", {}).get("muestra", 0) >= 2 and
        all(k in r.get("resumen", {}) for k in ("ocr", "llm", "llm_guard"))))
    candidates = []
    for path in root.glob("experiment_models_*.json"):
        if not path.is_file():
            continue
        record = _read(path)
        if record.get("results") and record.get("test_rows"):
            candidates.append((path, record))
    if not candidates:
        raise RuntimeError(f"No se encontraron experimentos de modelos en {root}")
    # Una competencia nueva ya contiene TODAS las familias. No leerla dos
    # veces como si fuera dos experimentos, ni mezclarla con otro snapshot.
    complete = [
        (path, report) for path, report in candidates
        if len(report["results"]) >= 50
        and {"rf_mae", "lstm"} <= {x.get("model") for x in report["results"]}
    ]
    if complete:
        chosen_path, _ = max(complete, key=lambda pair: (
            pair[0].stat().st_mtime, pair[0].name))
        classic = neural = chosen_path
    else:
        classical = [(p, r) for p, r in candidates
                     if len(r["results"]) >= 50 and
                     "rf_mae" in {x.get("model") for x in r["results"]}]
        neurons = [(p, r) for p, r in candidates
                   if len(r["results"]) >= 10 and
                   "lstm" in {x.get("model") for x in r["results"]}]
        compatible = []
        for cp, cr in classical:
            for np, nr in neurons:
                try:
                    _assert_same_holdout(cr, nr)
                except (KeyError, TypeError, ValueError):
                    continue
                compatible.append((cp, np))
        if not compatible:
            raise RuntimeError(
                "No existen resultados clásicos y neuronales compatibles: "
                "revisa fuente, población del test y referencias.")
        classic, neural = max(compatible, key=lambda pair: (
            max(pair[0].stat().st_mtime, pair[1].stat().st_mtime),
            min(pair[0].stat().st_mtime, pair[1].stat().st_mtime)))
    trained_candidates = sorted(root.glob("comparison_*.json"))
    trained = trained_candidates[-1] if trained_candidates else None
    return {"ocr": ocr, "classic": classic, "neural": neural, "trained": trained}


def _rounded(value, digits=2):
    return "" if value is None else round(float(value), digits)


def _chart(sheet_id: int, title: str, data_start: int, data_end: int,
           domain_col: int, value_cols: tuple[int, ...], anchor: int,
           unit: str, height: int = 410) -> dict:
    """Source rows use zero-based indices; headerCount=1 includes the header."""
    if data_end <= data_start + 1:
        raise ValueError("Un gráfico necesita encabezado y datos")

    def range_for(col: int) -> dict:
        return {"sources": [{
            "sheetId": sheet_id, "startRowIndex": data_start,
            "endRowIndex": data_end,
            "startColumnIndex": col, "endColumnIndex": col + 1,
        }]}
    return {
        "anchor_row": anchor, "height": height,
        "spec": {
            "title": title,
            "basicChart": {
                "chartType": "BAR",
                "legendPosition": "BOTTOM_LEGEND" if len(value_cols) > 1 else "NO_LEGEND",
                "headerCount": 1,
                "axis": [
                    {"position": "LEFT_AXIS", "title": "Método / modelo / campo"},
                    {"position": "BOTTOM_AXIS", "title": unit},
                ],
                "domains": [{"domain": {"sourceRange": range_for(domain_col)}}],
                "series": [
                    {"series": {"sourceRange": range_for(col)},
                     "targetAxis": "BOTTOM_AXIS"} for col in value_cols
                ],
            },
        },
    }


def ocr_comparison(report: dict, filename: str, sheet_id: int = 0):
    summary = report.get("resumen", {})
    sample = summary.get("muestra")
    if not isinstance(sample, int) or sample < 1:
        raise ValueError("Muestra OCR vacía o ausente")
    if not all(method in summary for method, _ in METHODS):
        raise ValueError("El reporte no tiene los tres métodos comparables")
    manual_n = int(summary.get("verificadas_a_mano") or 0)
    rows = [
        ["EcoData ECCI | Comparativa OCR vs LLM"],
        [f"Fuente: {filename} | Experimento: {report.get('generado', 'sin fecha')} | Semilla: {report.get('semilla', 'no disponible')}"],
        ["Limitación: la referencia proviene mayoritariamente del OCR; no es una prueba independiente de exactitud."],
        [f"Muestra total evaluada: {sample} facturas | Referencias cotejadas manualmente: {manual_n}"],
        [],
        ["Método", "Facturas", "Filas completas", "Filas completas (%)",
         "Tiempo promedio (s/factura)", "Errores/faltantes por campo", "Fuente"],
    ]
    for key, name in METHODS:
        data = summary[key]
        value = data.get("fila_completa")
        if value is None:
            raise ValueError(f"Sin métrica fila_completa de {key}")
        time_s = summary.get("segundos_ocr_media" if key == "ocr" else "segundos_llm_media")
        failures = sum((data.get(field) or {}).get("errores", 0) +
                       (data.get(field) or {}).get("faltantes", 0) for field in FIELDS)
        rows.append([name, sample, round(sample * float(value)), _rounded(float(value) * 100),
                     _rounded(time_s), failures, filename])

    rows.extend([
        [],
        ["Desglose por campo | exactitud frente a la referencia del experimento"],
        ["Campo", "Aciertos OCR", "OCR (%)", "Aciertos Gemini", "Gemini (%)",
         "Aciertos Gemini + reglas", "Gemini + reglas (%)", "Evaluaciones"],
    ])
    for field in FIELDS:
        entries = [summary[key][field] for key, _ in METHODS]
        evaluated = [sum(int(d.get(k, 0)) for k in ("aciertos", "errores", "faltantes"))
                     for d in entries]
        rows.append([field, entries[0]["aciertos"], _rounded(entries[0]["precision"] * 100),
                     entries[1]["aciertos"], _rounded(entries[1]["precision"] * 100),
                     entries[2]["aciertos"], _rounded(entries[2]["precision"] * 100),
                     min(evaluated)])

    rows.extend([
        [],
        [f"Submuestra cotejada visualmente (n={manual_n}); no representa una muestra aleatoria independiente"],
        ["Método", "Facturas manuales", "Filas completas (%)"],
    ])
    for key, name in METHODS:
        manual = summary.get(f"{key}_manual") or {}
        fraction = manual.get("fila_completa")
        rows.append([name, manual_n, _rounded(float(fraction) * 100) if fraction is not None else ""])

    # Galería visible debajo de las tablas, no en la columna R.
    # Las filas vacías son espacio real para gráficos de Google Sheets.
    rows.append(["GRÁFICOS | Extracción completa y concordancia por campo"])
    rows.extend([[] for _ in range(73)])
    charts = [
        _chart(sheet_id, "EcoData | Comparativa OCR: extracción completa", 5, 9,
               0, (3,), 27, "Facturas completas (%)", 430),
        _chart(sheet_id, "EcoData | Comparativa OCR: exactitud por campo", 11, 18,
               0, (2, 4, 6), 53, "Coincidencia por campo (%)", 520),
    ]
    return rows, charts, [5, 11, 20], {
        "muestra": sample, "manual": manual_n,
        "metodos": {name: round(100 * summary[key]["fila_completa"], 2)
                    for key, name in METHODS},
    }


def _canonical_source(value) -> str:
    return str(value or "").replace("\\", "/").lower()


def _assert_same_holdout(classic: dict, neural: dict) -> None:
    """No mezclar protocolos ni poblaciones por el simple hecho de tener MAE."""
    for field in ("test_rows", "validation_periods"):
        if classic.get(field) != neural.get(field):
            raise ValueError(f"Pruebas no comparables: difiere {field}")
    if _canonical_source(classic.get("source")) != _canonical_source(neural.get("source")):
        raise ValueError("Pruebas no comparables: fuentes históricas distintas")
    for base in ("persistence", "rolling_mean"):
        left = classic["baselines"][base]
        right = neural["baselines"][base]
        if left.get("n") != right.get("n") or abs(left["mae"] - right["mae"]) > 0.01:
            raise ValueError("Pruebas no comparables: cambia la cohorte de referencia")


def _detail(item, filename):
    score = item["test"]
    return [
        FAMILY_NAMES.get(item["model"], item["model"]),
        item["approach"], "Sí" if item.get("seasonal") else "No",
        json.dumps(item.get("params", {}), sort_keys=True, ensure_ascii=False),
        _rounded(item.get("validation_mae")), _rounded(score["mae"]),
        _rounded(score.get("rmse")), _rounded(score.get("wape")),
        _rounded(score.get("r2"), 4), int(score["n"]),
        _rounded((item.get("vs_persistence") or {}).get("p_better"), 4),
        "Sí" if item.get("significant") else "No", filename,
    ]


def models_comparison(classic: dict, neural: dict,
                      classic_filename: str, neural_filename: str,
                      trained: dict | None = None,
                      trained_filename: str = "", sheet_id: int = 0):
    _assert_same_holdout(classic, neural)
    all_items = [(x, classic_filename) for x in classic["results"]]
    if classic_filename != neural_filename:
        all_items += [(x, neural_filename) for x in neural["results"]]
    elif classic != neural:
        raise ValueError("Dos reportes diferentes no pueden compartir el mismo nombre")
    by_family = defaultdict(list)
    for entry, source in all_items:
        by_family[entry["model"]].append((entry, source))
    # Nunca elegir el mejor resultado del test: elegir por CV, de antemano.
    selected = {family: min(group, key=lambda pair: (
        float(pair[0]["validation_mae"]), pair[0]["approach"],
        json.dumps(pair[0].get("params", {}), sort_keys=True)))
        for family, group in by_family.items()}
    baseline = neural["baselines"]
    n = int(neural["test_rows"])
    rows = [
        ["EcoData ECCI | Comparativa de modelos predictivos"],
        [f"Test común: {n} observaciones | Validación: {', '.join(neural['validation_periods'])} | Histórico: {neural['source']}"],
        [f"{len(all_items)} configuraciones experimentales, {len(selected)} familias; cada representante se eligió por MAE de validación."],
        ["No seleccionar por el menor error de test. No hay champion promovido; comparación exploratoria."],
        [],
        ["Modelo / referencia", "Selección", "Enfoque", "CV MAE (kWh)",
         "Test MAE (kWh)", "Test RMSE (kWh)", "Test WAPE (%)",
         "Test R²", "N test", "P(mejor persistencia)", "Supera ref. con evidencia",
         "Tipo", "Reporte"],
    ]
    for label, key in (("Persistencia", "persistence"), ("Media móvil", "rolling_mean")):
        score = baseline[key]
        rows.append([label, "Referencia", "Referencia", "", _rounded(score["mae"]),
                     _rounded(score.get("rmse")), _rounded(score.get("wape")),
                     _rounded(score.get("r2"), 4), score["n"], "", "Referencia",
                     "Base", neural_filename])
    for family in sorted(selected):
        record, file = selected[family]
        test = record["test"]
        rows.append([
            FAMILY_NAMES.get(family, family),
            json.dumps(record.get("params", {}), ensure_ascii=False, sort_keys=True),
            record["approach"], _rounded(record["validation_mae"]),
            _rounded(test["mae"]), _rounded(test.get("rmse")),
            _rounded(test.get("wape")), _rounded(test.get("r2"), 4), test["n"],
            _rounded(record.get("vs_persistence", {}).get("p_better"), 4),
            "Sí" if record.get("significant") else "No",
            "Elegido por CV", file,
        ])
    chart_end = len(rows)
    charts = [
        _chart(sheet_id, "EcoData | Comparativa modelos: MAE test (n común)", 5,
               chart_end, 0, (4,), 25, "MAE (kWh)", 570),
        _chart(sheet_id, "EcoData | Comparativa modelos: WAPE test (n común)", 5,
               chart_end, 0, (6,), 57, "WAPE (%)", 570),
    ]
    rows.append(["GRÁFICOS | Familias elegidas por validación; menor MAE/WAPE es mejor"])
    rows.extend([[] for _ in range(67)])
    rows.extend([[], ["Detalle completo: todas las configuraciones (no seleccionar por test)"],
                 ["Familia", "Enfoque", "Estacionalidad", "Hiperparámetros",
                  "CV MAE (kWh)", "Test MAE (kWh)", "Test RMSE (kWh)",
                  "Test WAPE (%)", "Test R²", "N test",
                  "P(mejor persistencia)", "Significativo", "Fuente"]])
    detail_header = len(rows) - 1
    for record, source in sorted(all_items, key=lambda pair: (
            pair[0]["model"], pair[0]["approach"], pair[0]["validation_mae"])):
        rows.append(_detail(record, source))

    trained_count = 0
    skipped = []
    trained_header = None
    if trained:
        candidates = trained.get("candidates") or []
        comparable = []
        for candidate in candidates:
            score = candidate.get("metrics") or {}
            old_n = int(score.get("n") or -1)
            old_baseline = (candidate.get("baseline_metrics") or {}).get(
                "persistence", {}).get("mae")
            if old_n != n or old_baseline is None or abs(
                    old_baseline - baseline["persistence"]["mae"]) > 0.01:
                skipped.append((candidate.get("id", "sin ID"),
                                f"Test n={old_n} frente a n={n}; "
                                f"persistencia histórica={_rounded(old_baseline)} "
                                f"frente a {_rounded(baseline['persistence']['mae'])} kWh"))
                continue
            comparable.append(candidate)
        if comparable:
            rows.extend([[],
                ["Candidatos registrados v1 | test compatible; validación v1 diferente"],
                ["Modelo registrado", "Familia", "CV MAE", "Test MAE", "RMSE",
                 "WAPE (%)", "N test", "Estado", "Fuente"]])
            trained_header = len(rows) - 1
            for candidate in comparable:
                score = candidate["metrics"]
                rows.append([candidate["id"], candidate.get("model_type"),
                             _rounded((candidate.get("validation_metrics") or {}).get("mae")),
                             _rounded(score.get("mae")), _rounded(score.get("rmse")),
                             _rounded(score.get("wape")), score.get("n"),
                             "Challenger; sin promoción automática", trained_filename])
                trained_count += 1
        if skipped:
            rows.extend([[],
                [f"EXCLUSIONES | {len(skipped)} candidatos v1 históricos: test incompatible; métricas no comparables"],
                ["Modelo registrado", "Motivo de exclusión", "Fuente"]])
            for candidate_id, reason in skipped:
                rows.append([candidate_id, reason, trained_filename])
    headers = [5, detail_header] + ([trained_header] if trained_header is not None else [])
    summary = {
        "configuraciones": len(all_items), "familias": len(selected),
        "test_n": n, "candidatos_registrados": trained_count,
        "candidatos_no_comparables": len(skipped),
        "referencia_mae": _rounded(baseline["persistence"]["mae"]),
        "mejor_por_cv": {
            family: {"enfoque": item["approach"],
                     "mae_validacion": _rounded(item["validation_mae"]),
                     "mae_test": _rounded(item["test"]["mae"])}
            for family, (item, _) in selected.items()
        },
    }
    return rows, charts, headers, summary


def publish(write: bool = False, only: str = "all",
            ocr_path: Path | None = None, classic_path: Path | None = None,
            neural_path: Path | None = None, trained_path: Path | None = None):
    """Valida ambas comparaciones antes de escribir; evita publicaciones parciales."""
    defaults = default_reports() if any(p is None for p in (
        [ocr_path] if only == "ocr" else
        [classic_path, neural_path] if only == "models" else
        [ocr_path, classic_path, neural_path])) else {}
    path_ocr = ocr_path or defaults.get("ocr")
    path_classic = classic_path or defaults.get("classic")
    path_neural = neural_path or defaults.get("neural")
    path_trained = trained_path or defaults.get("trained")
    builders = []
    if only in ("all", "ocr"):
        if not path_ocr:
            raise RuntimeError("No se encontró reporte OCR/LLM.")
        record = _read(Path(path_ocr))
        builders.append((OCR_SHEET, lambda sid: ocr_comparison(
            record, Path(path_ocr).name, sid)))
    if only in ("all", "models"):
        if not (path_classic and path_neural):
            raise RuntimeError("Faltan reportes clásicos o neuronales.")
        classic = _read(Path(path_classic))
        neural = _read(Path(path_neural))
        trained = _read(Path(path_trained)) if path_trained else None
        builders.append((MODELS_SHEET, lambda sid: models_comparison(
            classic, neural, Path(path_classic).name, Path(path_neural).name,
            trained, Path(path_trained).name if path_trained else "", sid)))

    # VALIDAR todo antes de editar cualquier pestaña: si cambió la cohorte
    # del modelo o está incompleto el reporte OCR, no publicar resultados.
    previews = [(name, maker, maker(0)) for name, maker in builders]
    results = {}
    for name, _, (rows, charts, _, summary) in previews:
        results[name] = summary
        concise = summary if name == OCR_SHEET else {
            k: v for k, v in summary.items() if k != "mejor_por_cv"}
        print(f"{name}: {len(rows)} filas; {len(charts)} gráficos; resumen={concise}")
        if name == MODELS_SHEET and summary.get("candidatos_no_comparables"):
            print("AVISO: se excluyeron del ranking comparativo "
                  f"{summary['candidatos_no_comparables']} candidatos v1 "
                  "por usar una cohorte de test distinta. "
                  "Se muestran únicamente sus identificadores y el motivo.")
    if not write:
        print("Vista previa local: agrega --write para actualizar Google Sheets.")
        return results
    settings = get_ml_settings()
    sheets = SheetsService(settings.google_client_secret_file,
                           settings.google_token_file, settings.spreadsheet_id)
    for name, maker, _ in previews:
        sheets.ensure_sheet(name)
        sheet_id, _ = sheets.sheet_chart_info(name)
        rows, charts, headers, _ = maker(sheet_id)
        saved = sheets.replace_comparison(name, rows, charts, headers)
        print(f"Publicada: {name} | {saved['rows']} filas | {saved['charts']} gráficos")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compara OCR/LLM y modelos predictivos directamente en Google Sheets.")
    parser.add_argument("--write", action="store_true", help="Actualizar las dos pestañas reservadas")
    parser.add_argument("--only", choices=("all", "ocr", "models"), default="all")
    parser.add_argument("--ocr-report", type=Path)
    parser.add_argument("--classic-report", type=Path)
    parser.add_argument("--neural-report", type=Path)
    parser.add_argument("--trained-report", type=Path)
    args = parser.parse_args(argv)
    publish(args.write, args.only, args.ocr_report,
            args.classic_report, args.neural_report, args.trained_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
