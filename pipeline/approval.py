"""Aprobación humana por documento para propuestas verificadas.

Ningún PDF es movido ni escrito en Sheets. Se requiere revisión manual del
PDF antes de introducir la palabra APROBAR en la terminal por cada documento.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pipeline.pending import validate


def approve(proposals: list[dict], confirm=input, output=print) -> tuple[list[dict], dict]:
    """Devuelve una copia del lote con aprobaciones vinculadas al SHA-256."""
    result = []
    counts = {"aprobadas": 0, "rechazadas": 0, "no_elegibles": 0}
    for number, item in enumerate(proposals, 1):
        entry = dict(item)
        if entry.get("status") not in {"LISTA", "LISTA_LLM"} or not entry.get("pdf_sha256"):
            counts["no_elegibles"] += 1
            result.append(entry)
            continue
        problems = validate(entry.get("data") or {}, entry.get("texto") or "",
                            entry.get("nombre"), entry.get("campos_llm"),
                            entry.get("evidencias"))
        if problems:
            counts["no_elegibles"] += 1
            result.append(entry)
            continue
        data = entry["data"]
        output(f"\nDocumento {number}/{len(proposals)}: {entry.get('name')}")
        output(f"Cuenta: {data.get('Cuenta')}  Factura: {data.get('NumeroFactura')}")
        output(f"Periodo: {data.get('InicioPeriodo')} a {data.get('FinPeriodo')}")
        output(f"Consumo: {data.get('kWh')} kWh  Total a pagar: {data.get('ValorFacturado')} COP")
        output(f"PDF: https://drive.google.com/file/d/{entry['id']}/view")
        output("Verifique el PDF original. Esta acción NO moverá el archivo.")
        response = confirm("Escriba APROBAR para autorizar este documento, o Enter para omitir: ")
        if str(response).strip() == "APROBAR":
            entry["revision_manual"] = {
                "aprobada": True, "pdf_sha256": entry["pdf_sha256"],
                "fecha": datetime.now().isoformat(timespec="seconds"),
            }
            counts["aprobadas"] += 1
        else:
            entry.pop("revision_manual", None)
            counts["rechazadas"] += 1
        result.append(entry)
    return result, counts


def has_valid_approval(proposal: dict) -> bool:
    signed = proposal.get("revision_manual") or {}
    return (signed.get("aprobada") is True and
            bool(proposal.get("pdf_sha256")) and
            signed.get("pdf_sha256") == proposal.get("pdf_sha256"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Revisión humana interactiva, no mueve archivos.")
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("Aprobación individual requiere una terminal interactiva (TTY)")
    payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
    proposals = payload.get("propuestas", payload)
    if not isinstance(proposals, list):
        parser.error("El archivo no contiene una lista de propuestas")
    items, summary = approve(proposals)
    path = (Path(args.output) if args.output else Path("data/llm_proposals") /
            f"approved_{datetime.now():%Y%m%d_%H%M%S}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "origen": args.source, "resumen_aprobacion": summary, "propuestas": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Aprobaciones:", summary, "\nArchivo generado:", path)
    print("Todavía NO se movió ningún PDF. Aplicar requiere un comando separado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
