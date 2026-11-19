"""Auditoría de propuestas de 01C en modo exclusivamente de lectura.

No mueve archivos ni modifica Google Sheets. Comprueba estructura, duplicados,
metadatos actuales y (opcionalmente) huella SHA-256 de cada PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from pipeline.energy import invoice_key
from pipeline.pending import validate
from services.drive import DriveService
from services.sheets import SheetsService


def review(proposals: list[dict], registered: list[dict] | None = None,
           drive=None, verify_pdf: bool = False) -> dict:
    """Devuelve decisiones trazables sin hacer ninguna operación de escritura."""
    existing = registered or []
    ids = {str(row.get("ID") or "") for row in existing}
    keys = {invoice_key(row.get("Cuenta"), row.get("N° Factura", row.get("NumeroFactura")))
            for row in existing}
    results = []
    for entry in proposals:
        name = entry.get("name")
        document_id = entry.get("id")
        state = entry.get("status")
        problems = []
        if state not in {"LISTA", "LISTA_LLM"}:
            problems.append(f"estado de propuesta: {state}")
        else:
            data = entry.get("data") or {}
            problems.extend(validate(data, entry.get("texto", ""), entry.get("nombre"),
                                     entry.get("campos_llm"), entry.get("evidencias")))
            key = invoice_key(data.get("Cuenta"), data.get("NumeroFactura"))
            if document_id in ids or key in keys:
                problems.append("factura ya registrada en BD_energia")
            if not entry.get("pdf_sha256"):
                problems.append("sin huella PDF; regenerar propuesta")
        if drive is not None:
            try:
                current = drive.get_metadata(document_id)
                original = entry.get("parents") or []
                if (name != current.get("name")
                        or not original
                        or not set(original).intersection(current.get("parents") or [])):
                    problems.append("cambiaron nombre o carpeta en Drive")
                # Solo se descarga si la auditoría solicitó expresamente verificar PDFs.
                if verify_pdf and entry.get("pdf_sha256"):
                    actual = hashlib.sha256(drive.download(document_id)).hexdigest()
                    if actual != entry["pdf_sha256"]:
                        problems.append("cambió contenido del PDF (SHA-256)")
            except Exception as exc:
                problems.append(f"Drive inaccesible: {type(exc).__name__}")
        elif verify_pdf:
            problems.append("no puede verificar PDF sin conexión Drive")
        if not problems and drive is None:
            result = "VALIDACION_LOCAL"
        elif not problems and not verify_pdf:
            result = "METADATOS_OK_PDF_NO_VERIFICADO"
        elif not problems:
            result = "VERIFICADA"
        else:
            result = "REVISAR"
        results.append({
            "id": document_id,
            "archivo": name,
            "estado_original": state,
            "resultado": result,
            "motivos": problems,
        })
    counts = dict(Counter(item["resultado"] for item in results))
    return {"generado": datetime.now().isoformat(timespec="seconds"),
            "total": len(results), "resumen": counts, "documentos": results,
            "modo": {"drive": drive is not None, "verificar_pdf": verify_pdf}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audita propuestas sin mover facturas ni escribir en Sheets.")
    parser.add_argument("--from", dest="source", required=True, help="JSON de propuestas")
    parser.add_argument("--live", action="store_true", help="Consulta BD_energia y metadatos Drive")
    parser.add_argument("--verify-pdf", action="store_true", help="Descarga PDFs para validar SHA-256")
    parser.add_argument("--output", help="Ruta del informe JSON; por defecto reports/")
    args = parser.parse_args(argv)
    if args.verify_pdf and not args.live:
        parser.error("--verify-pdf requiere --live")
    payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
    proposals = payload.get("propuestas", payload)
    if not isinstance(proposals, list):
        parser.error("El documento no contiene una lista de propuestas")
    remote = None
    registered = None
    if args.live:
        s = get_settings()
        remote = DriveService(s.google_client_secret_file, s.google_token_file)
        sheets = SheetsService(s.google_client_secret_file, s.google_token_file,
                               s.spreadsheet_id)
        registered = sheets.get_rows(s.sheet_bd_energy)
    result = review(proposals, registered, remote, args.verify_pdf)
    output = Path(args.output) if args.output else (
        Path("reports") / f"auditoria_pendientes_{datetime.now():%Y%m%d_%H%M%S}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Auditoría de solo lectura:", result["total"], "documentos")
    for state, count in sorted(result["resumen"].items()):
        print(f"  {state}: {count}")
    print("Informe:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
