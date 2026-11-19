"""Compara la extracción local (PyMuPDF + reglas) con Gemini sobre facturas reales.

Referencia: los campos ya revisados de `BD_energia`, emparejados con el PDF original
por el ID de Drive. La comparación es reproducible: muestra determinista y semilla
fija. No escribe en Sheets ni mueve archivos.

Advertencia honesta: la referencia de la hoja la produjo el propio OCR y luego se
revisó a mano, así que favorece al OCR local. Sirve para medir cuánto aporta el LLM
en los campos que el OCR ya resolvía y cuánto cubre donde el OCR falla.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.config import get_ml_settings
from pipeline.invoice_rules import extract_invoice
from services.drive import DriveService
from services.llm import cross_check, extract_invoice as llm_extract, stated_total
from services.pdf_text import extract_text
from services.sheets import SheetsService

REPORTS = Path("reports")


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def number(value) -> float | None:
    text = str(value if value is not None else "").strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", text):
        text = re.sub(r"[.,]", "", text)
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def iso(value) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    return match.group(0) if match else None


def row_value(row: dict, *names: str) -> str:
    normalized = {"".join(ch for ch in str(k).lower() if ch.isalnum()): v for k, v in row.items()}
    for name in names:
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        if key in normalized:
            return str(normalized[key] or "").strip()
    return ""


def reference(row: dict) -> dict:
    return {
        "Cuenta": digits(row_value(row, "Cuenta")),
        "NumeroFactura": digits(row_value(row, "N° Factura", "NumeroFactura")),
        "kWh": number(row_value(row, "kWh", "Consumo")),
        "ValorFacturado": number(row_value(row, "ValorFacturado")),
        "InicioPeriodo": iso(row_value(row, "Inicio periodo")),
        "FinPeriodo": iso(row_value(row, "Fin periodo")),
    }


def file_id(row: dict, drive_ids: set[str]) -> str | None:
    for column in ("ID", "Link factura"):
        value = row_value(row, column)
        match = re.search(r"/d/([A-Za-z0-9_-]{10,})", value)
        candidate = match.group(1) if match else value
        if candidate in drive_ids:
            return candidate
    return None


def compare(ref: dict, data: dict) -> dict:
    """Un veredicto por campo: acierto, error o faltante."""
    verdict = {}
    for field in ("Cuenta", "NumeroFactura"):
        expected, got = digits(ref[field]), digits(data.get(field))
        verdict[field] = "faltante" if not got else "acierto" if got == expected else "error"
    for field in ("kWh", "ValorFacturado"):
        expected, got = ref[field], data.get(field)
        if got is None:
            verdict[field] = "faltante"
        elif expected is None:
            verdict[field] = "sin_referencia"
        elif field == "kWh":
            ok = abs(float(got) - float(expected)) <= max(0.5, 0.01 * abs(float(expected)))
            verdict[field] = "acierto" if ok else "error"
        else:
            verdict[field] = "acierto" if int(round(float(got))) == int(round(float(expected))) else "error"
    for field in ("InicioPeriodo", "FinPeriodo"):
        got = data.get(field)
        verdict[field] = "faltante" if not got else "acierto" if got == ref[field] else "error"
    return verdict


def summarize(records: list[dict], engine: str) -> dict:
    fields = ["Cuenta", "NumeroFactura", "kWh", "ValorFacturado", "InicioPeriodo", "FinPeriodo"]
    out = {}
    for field in fields:
        verdicts = [record["verdict"][engine][field] for record in records]
        evaluable = [v for v in verdicts if v != "sin_referencia"]
        out[field] = {
            "aciertos": evaluable.count("acierto"),
            "errores": evaluable.count("error"),
            "faltantes": evaluable.count("faltante"),
            "precision": round(evaluable.count("acierto") / len(evaluable), 4) if evaluable else None,
        }
    complete = [all(record["verdict"][engine][f] == "acierto" for f in fields) for record in records]
    out["fila_completa"] = round(sum(complete) / len(records), 4) if records else None
    return out


GOLD = Path("data/eval/gold_ocr_llm.json")


def load_gold(path: Path | None = None) -> dict:
    source = Path(path) if path else GOLD
    if not source.exists():
        return {}
    return json.loads(source.read_text(encoding="utf-8")).get("files", {})


FIELDS = ["Cuenta", "NumeroFactura", "kWh", "ValorFacturado", "InicioPeriodo", "FinPeriodo"]


def chart(summary: dict, path: Path) -> None:
    engines = [("ocr", "OCR local", "#1f3a93"),
               ("llm", "LLM solo", "#e67e22"),
               ("llm_guard", "LLM + regla", "#27ae60")]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [3, 1]})

    positions = range(len(FIELDS))
    width = 0.26
    for offset, (engine, label, color) in enumerate(engines):
        values = [(summary.get(engine, {}).get(field, {}) or {}).get("precision") or 0 for field in FIELDS]
        left.bar([p + (offset - 1) * width for p in positions], values, width, label=label, color=color)
    left.set_xticks(list(positions))
    left.set_xticklabels(["Cuenta", "Factura", "kWh", "Valor", "Inicio", "Fin"], fontsize=8)
    left.set_ylim(0, 1.05)
    left.set_ylabel("Precisión", fontsize=9)
    left.set_title("Precisión por campo", fontsize=10, fontweight="bold")
    left.legend(fontsize=7, frameon=False, loc="lower right")
    left.grid(axis="y", alpha=0.25)
    for side in ("top", "right"):
        left.spines[side].set_visible(False)

    labels = [label for _, label, _ in engines]
    times = [summary.get(key, {}).get("segundos_media") for key, _, _ in engines]
    values = [0 if t is None else t for t in times]
    bars = right.bar(labels, values, color=[color for _, _, color in engines], width=0.55)
    for bar, value in zip(bars, values):
        right.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.1f}s", ha="center", va="bottom", fontsize=7)
    right.set_title("Segundos por factura", fontsize=10, fontweight="bold")
    right.tick_params(labelsize=7)
    right.grid(axis="y", alpha=0.25)
    for side in ("top", "right"):
        right.spines[side].set_visible(False)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR local vs Gemini en facturas con referencia revisada.")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--model", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--gold", default=None, help="JSON con campos verificados a mano")
    args = parser.parse_args()

    settings = get_ml_settings()
    sheets = SheetsService(settings.google_client_secret_file, settings.google_token_file,
                           settings.spreadsheet_id)
    drive = DriveService(settings.google_client_secret_file, settings.google_token_file)
    from app.config import get_settings
    full = get_settings()

    rows = [row for row in sheets.get_rows(settings.sheet_bd_energy) if reference(row)["Cuenta"]]
    drive_ids = {item["id"] for item in drive.iter_pdfs(full.drive_02_energy)}
    candidates = [(row, file_id(row, drive_ids)) for row in rows]
    candidates = [(row, fid) for row, fid in candidates if fid]

    unseen, seen = [], set()
    for row, fid in candidates:
        if fid in seen:
            continue
        seen.add(fid)
        unseen.append((row, fid))

    random.Random(args.seed).shuffle(unseen)
    sample = unseen[: args.limit]
    gold = load_gold(args.gold)
    print(f"Referencia: {len(rows)} filas · {len(drive_ids)} PDFs en 02 · muestra {len(sample)} · "
          f"verificadas a mano {len(gold)}")

    records, started = [], time.perf_counter()
    for index, (row, fid) in enumerate(sample, 1):
        ref = reference(row)
        name = row_value(row, "Oficina")
        entry = {"id": fid, "sede": name, "reference": ref}
        if fid in gold:
            ref.update(gold[fid])
            entry["reference"] = ref
            entry["referencia"] = "manual"
        else:
            entry["referencia"] = "hoja"
        try:
            pdf = drive.download(fid)
        except Exception as exc:
            entry["error"] = f"descarga: {type(exc).__name__}"
            records.append(entry)
            print(f"[{index}/{len(sample)}] descarga fallida {fid}")
            continue

        local_start = time.perf_counter()
        text = ""
        try:
            text = extract_text(pdf, full.pdf_min_text_characters)
            data = extract_invoice(text)
            entry["ocr"] = {"data": {k: data.get(k) for k in ref}, "seconds": round(time.perf_counter() - local_start, 2)}
        except Exception as exc:
            entry["ocr"] = {"data": {}, "seconds": round(time.perf_counter() - local_start, 2),
                            "error": type(exc).__name__}

        llm = llm_extract(pdf, model=args.model)
        entry["llm"] = {"data": {k: llm.data.get(k) for k in ref}, "seconds": round(llm.elapsed_s, 2),
                        "model": llm.model, "error": llm.error, "warnings": llm.warnings,
                        "input_tokens": llm.input_tokens, "output_tokens": llm.output_tokens,
                        "confianza": llm.data.get("confianza"),
                        "evidencias": llm.data.get("evidencias"),
                        "contrastes": cross_check(llm.data, text) if text else []}
        if llm.error:
            entry["llm"]["data"] = {}

        # Sistema híbrido: el LLM propone y una regla determinista aplica el total que
        # el propio documento declara cuando existe. No usa la extracción OCR.
        guarded = dict(entry["llm"]["data"])
        stated = stated_total(text)
        if stated is not None:
            guarded["ValorFacturado"] = stated
        entry["llm_guardado"] = guarded

        entry["verdict"] = {
            "ocr": compare(ref, entry["ocr"]["data"]),
            "llm": compare(ref, entry["llm"]["data"]),
            "llm_guard": compare(ref, guarded),
        }
        records.append(entry)
        ok_ocr = sum(v == "acierto" for v in entry["verdict"]["ocr"].values())
        ok_llm = sum(v == "acierto" for v in entry["verdict"]["llm"].values())
        ok_guard = sum(v == "acierto" for v in entry["verdict"]["llm_guard"].values())
        print(f"[{index}/{len(sample)}] {ref['Cuenta'] or '?'} {ref['InicioPeriodo']} "
              f"aciertos OCR={ok_ocr}/6 LLM={ok_llm}/6 +regla={ok_guard}/6 "
              f"t={entry['ocr']['seconds']}s/{entry['llm']['seconds']}s "
              f"conf={entry['llm']['confianza']}" + (f" error={llm.error[:60]}" if llm.error else ""))

    usable = [record for record in records if "verdict" in record]
    manual = [record for record in usable if record.get("referencia") == "manual"]
    summary = {
        "muestra": len(usable),
        "fallidas": len(records) - len(usable),
        "verificadas_a_mano": len(manual),
        "segundos_totales": round(time.perf_counter() - started, 1),
        "ocr": summarize(usable, "ocr") if usable else {},
        "llm": summarize(usable, "llm") if usable else {},
        "llm_guard": summarize(usable, "llm_guard") if usable else {},
        "ocr_manual": summarize(manual, "ocr") if manual else {},
        "llm_manual": summarize(manual, "llm") if manual else {},
        "llm_guard_manual": summarize(manual, "llm_guard") if manual else {},
        "tokens_entrada": sum(record["llm"].get("input_tokens") or 0 for record in usable),
        "tokens_salida": sum(record["llm"].get("output_tokens") or 0 for record in usable),
        "segundos_llm_media": round(sum(record["llm"]["seconds"] for record in usable) / len(usable), 2) if usable else None,
        "segundos_ocr_media": round(sum(record["ocr"]["seconds"] for record in usable) / len(usable), 2) if usable else None,
    }
    if usable:
        summary["ocr"]["segundos_media"] = summary["segundos_ocr_media"]
        summary["llm"]["segundos_media"] = summary["segundos_llm_media"]
        summary["llm_guard"]["segundos_media"] = summary["segundos_llm_media"]

    output = Path(args.output) if args.output else REPORTS / f"ocr_vs_llm_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    model_used = next((record["llm"]["model"] for record in usable if record["llm"].get("model")), args.model)
    output.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "modelo": model_used,
        "semilla": args.seed,
        "nota": "Referencia = campos revisados de BD_energia; el OCR local produjo esa base, "
                "así que la comparación favorece al OCR. 'llm_guard' aplica el total que el "
                "documento declara (TOTAL A PAGAR) cuando el LLM propone otro. Las filas con "
                "referencia 'manual' fueron verificadas contra la imagen del PDF.",
        "resumen": summary,
        "registros": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    frame = pd.DataFrame({field: {
        engine: summary[engine].get(field, {}).get("precision")
        for engine in ("ocr", "llm", "llm_guard")
    } for field in ("Cuenta", "NumeroFactura", "kWh", "ValorFacturado", "InicioPeriodo", "FinPeriodo")}).T
    print("\nPrecisión por campo (1.0 = todos los aciertos sobre los evaluables)")
    print(frame.to_string(float_format=lambda value: f"{value:6.1%}" if pd.notna(value) else "   —"))
    print(f"Fila completa: OCR {summary['ocr'].get('fila_completa', 0):.1%} · "
          f"LLM {summary['llm'].get('fila_completa', 0):.1%} · "
          f"LLM+regla {summary['llm_guard'].get('fila_completa', 0):.1%}")
    if manual:
        print(f"Solo verificadas a mano (n={len(manual)}): "
              f"OCR {summary['ocr_manual'].get('fila_completa', 0):.1%} · "
              f"LLM {summary['llm_manual'].get('fila_completa', 0):.1%} · "
              f"LLM+regla {summary['llm_guard_manual'].get('fila_completa', 0):.1%}")
    print(f"Tiempo medio: OCR {summary['segundos_ocr_media']}s · LLM {summary['segundos_llm_media']}s")
    print(f"Tokens: entrada {summary['tokens_entrada']:,} · salida {summary['tokens_salida']:,}")
    chart(summary, output.with_suffix(".png"))
    print(f"Reporte: {output}")
    print(f"Gráfico: {output.with_suffix('.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
