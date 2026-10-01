from __future__ import annotations

import logging

from pipeline.common import complete, safe_filename_piece
from pipeline.invoice_rules import extract_invoice

logger = logging.getLogger(__name__)
ENERGY_HEADERS = ["ID", "Código", "Oficina", "Cuenta", "N° Factura", "kWh", "Año", "Mes",
                  "Inicio periodo", "Fin periodo", "Link factura", "Observaciones", "ValorFacturado"]
ADJUSTMENT_HEADERS = ENERGY_HEADERS[:-1] + ["Estado", "ValorFacturado"]


def process_energy(ctx, drive, sheets, settings):
    file_id = ctx["id"]
    data = extract_invoice(ctx["ocrText"])
    data.update(ID=file_id, LinkFactura=ctx.get("webViewLink", ""), Oficina=ctx.get("sourceOffice") or None)
    if not data['Oficina']:
        data['Observaciones'] = ' | '.join(filter(None, [data['Observaciones'], 'Sede no identificada desde la carpeta de origen']))

    if complete(data):
        office_folder = drive.ensure_folder(
            settings.drive_02_energy,
            safe_filename_piece(data["Oficina"]),
        )
        drive.move(file_id, office_folder["id"])

        filename = (
            f"{safe_filename_piece(data['Oficina'])} - "
            f"{safe_filename_piece(data['Cuenta'])} - "
            f"{safe_filename_piece(data['Mes'])}/{safe_filename_piece(data['Año'])}.pdf"
        )
        drive.rename(file_id, filename)

        sheets.append(settings.sheet_bd_energy, [
            data["ID"],
            data["Codigo"],
            data["Oficina"],
            data["Cuenta"],
            data["NumeroFactura"],
            data["kWh"],
            data["Año"],
            data["Mes"],
            data["InicioPeriodo"],
            data["FinPeriodo"],
            data["LinkFactura"],
            data["Observaciones"],
            data["ValorFacturado"],
        ], headers=ENERGY_HEADERS)
        logger.info("Factura de energía procesada: %s", file_id)
        return {"status": "PROCESADA", "data": data}

    drive.move(file_id, settings.drive_01c)
    filename = (
        f"ENERGIA_PENDIENTE - "
        f"{safe_filename_piece(data.get('Oficina'))} - "
        f"{safe_filename_piece(data.get('Cuenta'))} - "
        f"{safe_filename_piece(data.get('Mes'))}-"
        f"{safe_filename_piece(data.get('Año'))}.pdf"
    )
    drive.rename(file_id, filename)

    sheets.append(settings.sheet_adjust_energy, [
        data["ID"],
        data["Codigo"],
        data["Oficina"],
        data["Cuenta"],
        data["NumeroFactura"],
        data["kWh"],
        data["Año"],
        data["Mes"],
        data["InicioPeriodo"],
        data["FinPeriodo"],
        data["LinkFactura"],
        data["Observaciones"],
        "Pendiente",
        data["ValorFacturado"],
    ], headers=ADJUSTMENT_HEADERS)
    logger.warning("Factura de energía enviada a ajuste manual: %s", file_id)
    return {"status": "PENDIENTE", "data": data}
