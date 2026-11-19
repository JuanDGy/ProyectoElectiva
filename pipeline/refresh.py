"""Renueva propuestas antiguas con huella PDF y OCR actual; no usa Gemini ni escribe en Google.

Una propuesta solo recibe SHA-256 cuando el OCR del PDF actual coincide con
el texto registrado. Diferencias quedan en REVISAR, no se aplica ninguna factura.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from pipeline.pending import validate
from services.drive import DriveService
from services.pdf_text import extract_text


def normalize_document(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def refresh(proposals: list[dict], drive, minimum_characters: int,
            limit: int | None = None) -> tuple[list[dict], dict]:
    """Procesa como máximo limit propuestas de estado listo; no altera el origen."""
    out = [dict(item) for item in proposals]
    stats = Counter()
    checked = 0
    for entry in out:
        if entry.get("status") not in {"LISTA", "LISTA_LLM"}:
            stats["ignoradas"] += 1
            continue
        if limit is not None and checked >= limit:
            stats["fuera_de_limite"] += 1
            continue
        checked += 1
        errors = validate(entry.get("data") or {}, entry.get("texto") or "",
                          entry.get("nombre"), entry.get("campos_llm"),
                          entry.get("evidencias"))
        if errors:
            entry["status"] = "REVISAR"
            entry["errores"] = sorted(set(entry.get("errores", []) + errors))
            stats["invalidas"] += 1
            continue
        try:
            metadata = drive.get_metadata(entry["id"])
            if (metadata.get("name") != entry.get("name")
                    or not set(metadata.get("parents", [])) & set(entry.get("parents", []))):
                entry["status"] = "REVISAR"
                entry["errores"] = list(entry.get("errores") or []) + [
                    "nombre o carpeta de PDF cambiaron en Drive"]
                stats["movidas"] += 1
                continue
            contents = drive.download(entry["id"])
            current_text = extract_text(contents, minimum_characters)
            if normalize_document(current_text) != normalize_document(entry.get("texto", "")):
                entry["status"] = "REVISAR"
                entry["errores"] = list(entry.get("errores") or []) + [
                    "texto OCR cambió; es necesario generar nueva propuesta"]
                stats["texto_diferente"] += 1
                continue
            entry["pdf_sha256"] = hashlib.sha256(contents).hexdigest()
            entry["verificado_en"] = datetime.now().isoformat(timespec="seconds")
            stats["verificadas"] += 1
        except Exception as exc:
            entry["status"] = "REVISAR"
            entry["errores"] = list(entry.get("errores") or []) + [
                f"Error al verificar PDF: {type(exc).__name__}"]
            stats["errores_red_ocr"] += 1
    return out, dict(stats)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Renueva huellas PDF en un JSON de propuestas, sin escrituras remotas.")
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit debe ser positivo")
    payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
    original = payload.get("propuestas", payload)
    if not isinstance(original, list):
        parser.error("No se encontró lista de propuestas")
    settings = get_settings()
    drive = DriveService(settings.google_client_secret_file, settings.google_token_file)
    renewed, stats = refresh(original, drive, settings.pdf_min_text_characters, args.limit)
    output = (Path(args.output) if args.output else Path("data/llm_proposals") /
              f"verified_{datetime.now():%Y%m%d_%H%M%S}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "origen": str(args.source),
        "resumen_renovacion": stats,
        "propuestas": renewed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Propuestas renovadas sin modificaciones en Drive ni Sheets:", stats)
    print("Archivo:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
