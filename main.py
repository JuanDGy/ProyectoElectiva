from __future__ import annotations

import logging
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from services.drive import DriveService
from services.sheets import SheetsService
from services.pdf_text import extract_text
from pipeline.triage import classify
from pipeline.energy import invoice_key, process_energy


HISTORY_MANIFEST = Path("data/history_manifest.jsonl")


def _configure_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run_next(source_file_id: str | None = None) -> int | None:
    """Procesa un PDF; None indica que la bandeja está vacía."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("ecodata")

    drive = DriveService(
        settings.google_client_secret_file,
        settings.google_token_file,
    )
    sheets = SheetsService(
        settings.google_client_secret_file,
        settings.google_token_file,
        settings.spreadsheet_id,
    )
    if source_file_id:
        original = drive.get_metadata(source_file_id)
        if original.get("mimeType") != "application/pdf":
            raise ValueError("El archivo de origen debe ser un PDF")
        office = drive.source_office(original, settings.drive_history_root_id)
        file_meta = drive.copy(source_file_id, original["name"], settings.drive_01a, source_office=office)
    else:
        file_meta = drive.list_one_pdf(settings.drive_01a)
    if not file_meta:
        logger.info("No hay PDFs pendientes en 01A.")
        return None

    file_id = file_meta["id"]
    source_metadata = drive.get_metadata(file_id)
    source_office = drive.source_office(source_metadata, settings.drive_history_root_id)
    if not source_office:
        source_office = drive.source_office(source_metadata, settings.drive_01a)
    if source_office and not source_metadata.get("appProperties", {}).get("sourceOffice"):
        drive.remember_source_office(source_metadata, source_office)
    original_id = source_metadata.get("appProperties", {}).get("sourceFileId")
    source_link = f"https://drive.google.com/file/d/{original_id}/view" if original_id else file_meta.get("webViewLink", "")
    logger.info("Tomando PDF: %s (%s)", file_meta["name"], file_id)
    if source_office:
        logger.info("Sede de origen: %s", source_office)

    # Marcar en procesamiento
    drive.move(file_id, settings.drive_01b)
    file_meta = drive.get_metadata(file_id)

    try:
        pdf = drive.download(file_id)
        ocr_text = extract_text(pdf, settings.pdf_min_text_characters)
        invoice_type = classify(file_meta["name"], ocr_text)

        logger.info("TRIAGE %s => %s", file_meta["name"], invoice_type)

        ctx = {
            "id": file_id,
            "name": file_meta["name"],
            "webViewLink": source_link,
            "ocrText": ocr_text,
            "sourceOffice": source_office,
        }

        if invoice_type == "ENERGIA":
            process_energy(ctx, drive, sheets, settings)

        else:
            drive.move(file_id, settings.drive_01d)
            logger.warning("Documento SIN_CLASIFICAR enviado a 01D: %s", file_id)

        return 0

    except Exception:
        logger.exception("Error técnico procesando %s; se intentará devolver a 01A", file_id)
        try:
            # Si el archivo ya terminó en carpeta final/01C, esta operación no se debe
            # ejecutar ciegamente. Verificamos si sigue en 01B.
            meta = drive.get_metadata(file_id)
            if settings.drive_01b in meta.get("parents", []):
                drive.move(file_id, settings.drive_01a)
        except Exception:
            logger.exception("No fue posible restaurar el original a 01A")
        return 1


def run_once(source_file_id: str | None = None) -> int:
    result = run_next(source_file_id)
    return 0 if result is None else result


def run_batch(limit: int | None = 1) -> int:
    """Procesa hasta limit PDFs; None permite vaciar la bandeja."""
    if limit is not None and limit < 1:
        raise ValueError("La cantidad de documentos debe ser mayor que cero")
    logger = logging.getLogger("ecodata")
    processed = 0
    while limit is None or processed < limit:
        result = run_next()
        if result is None:
            break
        if result != 0:
            logger.error("Lote detenido por un error técnico. Documentos completados: %s", processed)
            return result
        processed += 1
        logger.info("Documentos completados en este lote: %s", processed)
    logger.info("Lote finalizado. Documentos completados: %s", processed)
    return 0


def _row_value(row: dict, *names: str) -> str:
    normalized = {
        "".join(ch for ch in str(key).lower() if ch.isalnum()): value
        for key, value in row.items()
    }
    for name in names:
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        if key in normalized:
            return str(normalized[key] or "").strip()
    return ""


def registered_invoices(sheets: SheetsService, settings) -> set[tuple[str, str]]:
    """Identidad de las facturas ya escritas en BD_energia."""
    return {
        invoice_key(_row_value(row, "Cuenta"), _row_value(row, "N° Factura", "NumeroFactura"))
        for row in sheets.get_rows(settings.sheet_bd_energy)
    }


def history_done_ids(path: Path = HISTORY_MANIFEST) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("id"):
            done.add(record["id"])
    return done


def record_history(entry: dict, path: Path = HISTORY_MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_history(limit: int | None = None) -> int:
    """Importa el histórico completo sin copiar ni mover los originales.

    Deduplica por cuenta + número de factura antes de escribir y guarda cada
    archivo tratado en un manifiesto local para poder reanudar. Los errores no
    se registran, de modo que una nueva ejecución los reintenta.
    """
    settings = get_settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger("ecodata")

    drive = DriveService(
        settings.google_client_secret_file,
        settings.google_token_file,
    )
    sheets = SheetsService(
        settings.google_client_secret_file,
        settings.google_token_file,
        settings.spreadsheet_id,
    )

    manifest = HISTORY_MANIFEST
    done = history_done_ids(manifest)
    known = registered_invoices(sheets, settings)
    logger.info("Histórico: %s facturas ya en BD, %s archivos ya tratados", len(known), len(done))

    attempted = imported = duplicated = failed = skipped = 0
    for item in drive.iter_pdfs(settings.drive_history_root_id):
        if limit is not None and attempted >= limit:
            break
        if item["id"] in done:
            skipped += 1
            continue
        attempted += 1
        stamp = datetime.now().isoformat(timespec="seconds")
        try:
            pdf = drive.download(item["id"])
            ocr_text = extract_text(pdf, settings.pdf_min_text_characters)
            invoice_type = classify(item["name"], ocr_text)
            if invoice_type != "ENERGIA":
                record_history({"id": item["id"], "name": item["name"], "status": "NO_ENERGIA", "timestamp": stamp}, manifest)
                continue

            ctx = {
                "id": item["id"],
                "name": item["name"],
                "webViewLink": f"https://drive.google.com/file/d/{item['id']}/view",
                "ocrText": ocr_text,
                "sourceOffice": drive.source_office(item, settings.drive_history_root_id),
            }
            result = process_energy(ctx, drive, sheets, settings, archive=False, known=known)
            if result["status"] == "DUPLICADA":
                duplicated += 1
            elif result["status"] == "PROCESADA":
                imported += 1
            record_history({
                "id": item["id"],
                "name": item["name"],
                "status": result["status"],
                "cuenta": result["data"].get("Cuenta"),
                "factura": result["data"].get("NumeroFactura"),
                "sede": result["data"].get("Oficina"),
                "anio": result["data"].get("Año"),
                "mes": result["data"].get("Mes"),
                "timestamp": stamp,
            }, manifest)
            logger.info("Histórico %s => %s", item["name"], result["status"])
        except Exception:
            failed += 1
            logger.exception("Error importando %s (%s); se reintentará", item["name"], item["id"])

    logger.info(
        "Histórico finalizado. Intentados: %s | Importados: %s | Duplicados: %s | Fallidos: %s | Ya tratados: %s",
        attempted, imported, duplicated, failed, skipped,
    )
    return 1 if failed else 0


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Debe ser un número entero positivo")
    if number < 1:
        raise argparse.ArgumentTypeError("Debe ser un número entero positivo")
    return number


def cli() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--file-id", help="PDF del histórico: procesa una copia y conserva la sede de su carpeta")
    mode.add_argument("--limit", type=positive_int, default=1, metavar="N", help="Procesar hasta N PDFs pendientes (por defecto: 1)")
    mode.add_argument("--all", action="store_true", help="Procesar todos los PDFs pendientes en 01A y sus subcarpetas")
    parser.add_argument(
        "--history",
        action="store_true",
        help="Importar el histórico completo (DRIVE_HISTORY_ROOT_ID) sin copiar ni mover los originales; "
             "combina con --limit o --all",
    )
    args = parser.parse_args()
    if args.history and args.file_id:
        parser.error("--history no se puede combinar con --file-id")
    if args.history:
        return run_history(None if args.all else args.limit)
    if args.file_id:
        return run_once(args.file_id)
    return run_batch(None if args.all else args.limit)


if __name__ == "__main__":
    sys.exit(cli())
