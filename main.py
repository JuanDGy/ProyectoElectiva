from __future__ import annotations

import logging
import argparse
import sys

from app.config import get_settings
from services.drive import DriveService
from services.sheets import SheetsService
from services.pdf_text import extract_text
from pipeline.triage import classify
from pipeline.energy import process_energy


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
    args = parser.parse_args()
    if args.file_id:
        return run_once(args.file_id)
    return run_batch(None if args.all else args.limit)


if __name__ == "__main__":
    sys.exit(cli())
