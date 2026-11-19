"""Revisión asistida de la cola de pendientes (01C) con propuesta y aplicación separadas.

El OCR local corre primero. Solo si quedan campos vacíos se consulta al LLM. Toda
propuesta se valida contra el texto del documento y contra el nombre del archivo
(que la propia tubería escribió) antes de poder aplicarse. Aplicar mueve el PDF a
`02_FACTURAS_ENERGIA/<sede>/` y escribe en `BD_energia`; lo que no valida se queda
en 01C y se lista con el motivo.

Uso:
    python -m pipeline.pending --limit 50
    python -m pipeline.pending --apply --from data/llm_proposals/pending_FECHA.json

Sin `--apply` no se mueve ni se escribe nada.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
import logging
import re
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from pipeline.common import safe_filename_piece
from pipeline.energy import ENERGY_HEADERS, invoice_key
from pipeline.invoice_rules import extract_invoice
from pipeline.triage import classify
from services.drive import DriveService
from services.llm import LLMError, cross_check, extract_invoice as llm_extract, stated_total
from services.pdf_text import extract_text
from services.sheets import SheetsService

logger = logging.getLogger("ecodata.pending")

REQUIRED = ["Oficina", "Cuenta", "NumeroFactura", "kWh", "ValorFacturado",
            "InicioPeriodo", "FinPeriodo", "Año", "Mes"]
NAME = re.compile(
    r"^ENERGIA_PENDIENTE\s*-\s*(?P<sede>.+?)\s*-\s*(?P<cuenta>\d+)\s*-\s*"
    r"(?P<mes>\d{1,2})-(?P<anio>\d{4})\.pdf$",
    re.I,
)
OUTPUT = Path("data/llm_proposals")
EXCLUDED_LLM = {"Oficina"}  # la sede no se deduce del texto: viene del nombre
# Banda de plausibilidad del precio implícito (COP por kWh). En las facturas ENEL de
# ECCI el valor real ronda 500-1.500; por fuera de 50-20.000 el número no es un total
# de energía creíble. La banda ancha solo bloquea; la estrecha avisa para revisión.
PRICE_BLOCK = (50.0, 20_000.0)
PRICE_WARN = (300.0, 3_000.0)
# ENEL emite facturas de $0 con una etiqueta propia, sin "TOTAL A PAGAR".
ZERO_MARKERS = re.compile(r"FACTURA\s+SIN\s+COBRO|SIN\s+VALOR\s+A\s+PAGAR", re.I)
# Nombres de campo que usa el prompt del LLM.
LLM_NAMES = {
    "Cuenta": "cuenta",
    "NumeroFactura": "factura",
    "kWh": "kwh",
    "ValorFacturado": "valor_facturado",
    "InicioPeriodo": "inicio_periodo",
    "FinPeriodo": "fin_periodo",
}


def parse_pending_name(name: str) -> dict | None:
    """Datos que la tubería dejó en el nombre del archivo pendiente."""
    match = NAME.match(str(name or "").strip())
    if not match:
        return None
    return {
        "Oficina": match.group("sede").strip(),
        "Cuenta": match.group("cuenta"),
        "Mes": match.group("mes").zfill(2),
        "Año": int(match.group("anio")),
    }


def _missing(data: dict) -> list[str]:
    return [field for field in REQUIRED if data.get(field) in (None, "")]


def merge_fields(local: dict, proposed: dict, allowed: set[str] | None = None) -> tuple[dict, list[str]]:
    """Solo rellena vacíos: nunca sobrescribe un valor del OCR local."""
    allowed = allowed if allowed is not None else set(REQUIRED) - EXCLUDED_LLM
    final = dict(local)
    used = []
    for field in REQUIRED:
        if final.get(field) in (None, "") and field in allowed:
            value = proposed.get(field)
            if value not in (None, ""):
                final[field] = value
                used.append(field)
    return final, used


def _evidence(data: dict) -> list[dict]:
    return list(data.get("evidencias") or [])


def plausibility(data: dict) -> tuple[list[str], list[str]]:
    """Coherencia entre el total, el consumo y lo que el documento declara.

    Devuelve (bloqueos, avisos). Un total que no es un valor a pagar —negativo, cero
    sin respaldo, o absurdo frente al consumo— no puede entrar a `BD_energia`: el
    modelo predictivo aprende de esa columna.
    """
    errors: list[str] = []
    warnings: list[str] = []
    total = data.get("ValorFacturado")
    kwh = data.get("kWh")
    if total is None:
        return errors, warnings
    total = float(total)

    if total < 0:
        errors.append(f"total negativo ({total:g}): el documento muestra un saldo a favor, "
                      f"no un valor a pagar")
        return errors, warnings

    if total == 0:
        # El cero solo es válido si el documento lo declara; eso lo comprueba validate().
        return errors, warnings
    if kwh is None:
        return errors, warnings
    kwh = float(kwh)

    if kwh == 0:
        if total > 0:
            warnings.append(f"consumo en cero con cobro de {total:g}: puede ser cargo fijo "
                            f"o un consumo mal leído")
        return errors, warnings

    price = total / kwh
    if total > 0 and not (PRICE_BLOCK[0] <= price <= PRICE_BLOCK[1]):
        errors.append(f"total {total:g} para {kwh:g} kWh implica {price:,.0f} COP/kWh, "
                      f"fuera de cualquier tarifa creíble: el total o el consumo está mal")
    elif total > 0 and not (PRICE_WARN[0] <= price <= PRICE_WARN[1]):
        warnings.append(f"total {total:g} para {kwh:g} kWh implica {price:,.0f} COP/kWh; "
                        f"conviene revisar el documento")
    return errors, warnings


def validate(data: dict, text: str, name_info: dict | None = None,
             llm_fields: list[str] | None = None, evidence: list[dict] | None = None) -> list[str]:
    """Errores que impiden mover el archivo. Vacío significa aplicable."""
    errors = []
    missing = _missing(data)
    if missing:
        errors.append("faltan campos: " + ", ".join(missing))
        return errors

    digits_text = re.sub(r"\D", "", text or "")
    if name_info:
        if str(data["Cuenta"]).strip() != str(name_info["Cuenta"]).strip():
            errors.append(f"la cuenta {data['Cuenta']} no coincide con el nombre del archivo "
                          f"({name_info['Cuenta']})")
        if int(data["Año"]) != int(name_info["Año"]) or int(data["Mes"]) != int(name_info["Mes"]):
            errors.append(f"el período {data['Mes']}/{data['Año']} no coincide con el nombre "
                          f"({name_info['Mes']}/{name_info['Año']})")

    stated = stated_total(text or "")
    # El total del OCR local tiene su propio parser (con bloqueadores) y no se
    # cuestiona aquí; solo se bloquea un total que venga del LLM y contradiga
    # la etiqueta TOTAL A PAGAR del documento.
    if stated is not None and llm_fields and "ValorFacturado" in llm_fields \
            and abs(float(data["ValorFacturado"]) - stated) > 0.5:
        errors.append(f"el documento dice TOTAL A PAGAR {stated:g} y la fila tiene "
                      f"{float(data['ValorFacturado']):g}")

    # Un cero solo se acepta si el documento lo dice con todas las letras.
    if float(data["ValorFacturado"]) == 0 and stated != 0 and not ZERO_MARKERS.search(text or ""):
        errors.append("total en cero sin 'TOTAL A PAGAR $0' ni 'FACTURA SIN COBRO' "
                      "en el documento")

    errors.extend(plausibility(data)[0])

    if llm_fields:
        quotes = {str(item.get("campo", "")).strip().lower(): str(item.get("cita") or "").strip()
                  for item in (evidence or []) if str(item.get("cita") or "").strip()}
        # Se permiten diferencias de formato; no se confía en citas inventadas.
        def compact(value):
            return re.sub(r"[^0-9a-záéíóúñ]+", "", (value or "").lower())
        normalized_text = compact(text)
        for field in llm_fields:
            quote = quotes.get(LLM_NAMES.get(field, field.lower()))
            if not quote:
                errors.append(f"sin evidencia literal para {field}")
            elif not compact(quote) or compact(quote) not in normalized_text:
                errors.append(f"cita de {field} no verificable en texto OCR; revisar PDF")

    if llm_fields and digits_text:
        if "NumeroFactura" in llm_fields and re.sub(r"\D", "", str(data["NumeroFactura"])) not in digits_text:
            errors.append("el número de factura no aparece en el texto del PDF")
        if "Cuenta" in llm_fields and re.sub(r"\D", "", str(data["Cuenta"])) not in digits_text:
            errors.append("la cuenta no aparece en el texto del PDF")

    if str(data["InicioPeriodo"]) > str(data["FinPeriodo"]):
        errors.append("período invertido")
    return errors


def _row(data: dict) -> list:
    return [data["ID"], data.get("Codigo"), data["Oficina"], data["Cuenta"],
            data["NumeroFactura"], data["kWh"], data["Año"], data["Mes"],
            data["InicioPeriodo"], data["FinPeriodo"], data.get("LinkFactura"),
            data.get("Observaciones"), data["ValorFacturado"]]


def propose(limit: int | None = None, model: str | None = None,
            source_id: str | None = None, use_llm: bool = True) -> list[dict]:
    """Recorre 01C y produce propuestas. No mueve ni escribe en Sheets."""
    settings = get_settings()
    drive = DriveService(settings.google_client_secret_file, settings.google_token_file)
    source = source_id or settings.drive_01c

    proposals = []
    for index, item in enumerate(drive.iter_pdfs(source), 1):
        if limit is not None and len(proposals) >= limit:
            break
        proposal = {"id": item["id"], "name": item.get("name"), "parents": item.get("parents", [])}
        try:
            pdf = drive.download(item["id"])
            proposal["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
            text = extract_text(pdf, settings.pdf_min_text_characters)
            kind = classify(item.get("name", ""), text)
            name_info = parse_pending_name(item.get("name", ""))
            local = extract_invoice(text)
            local.update(ID=item["id"],
                         LinkFactura=f"https://drive.google.com/file/d/{item['id']}/view",
                         Oficina=(name_info or {}).get("Oficina"))
            proposal.update({"tipo": kind, "nombre": name_info, "texto": text,
                             "local": {k: local.get(k) for k in REQUIRED},
                             "local_observaciones": local.get("Observaciones")})

            if kind != "ENERGIA":
                proposal.update(status="NO_ENERGIA", data={},
                                errores=["el texto no parece una factura de energía"])
                proposals.append(proposal)
                print(f"[{index}] {item.get('name')} -> NO_ENERGIA")
                continue

            final = dict(local)
            llm_fields, rule_fields, evidence, warnings = [], [], [], []
            stated = stated_total(text)
            if stated is not None and final.get("ValorFacturado") in (None, ""):
                # La regla determinista llena el total sin gastar tokens.
                final["ValorFacturado"] = stated
                rule_fields.append("ValorFacturado")

            if use_llm and _missing(final):
                try:
                    result = llm_extract(pdf, model=model)
                    if result.error:
                        warnings.append(f"LLM: {result.error}")
                    else:
                        final, got = merge_fields(final, result.data)
                        evidence = _evidence(result.data)
                        warnings.extend(result.warnings)
                        warnings.extend(cross_check(result.data, text))
                        proposal["modelo"] = result.model
                        proposal["segundos_llm"] = round(result.elapsed_s, 2)
                        if ("ValorFacturado" in got and stated is not None
                                and abs(float(final["ValorFacturado"]) - stated) > 0.5):
                            # El documento manda: el LLM propone, la etiqueta decide.
                            final["ValorFacturado"] = stated
                            got.remove("ValorFacturado")
                            rule_fields.append("ValorFacturado")
                            warnings.append("el LLM propuso un total distinto al TOTAL A PAGAR "
                                             "del documento; se aplicó el del documento")
                        llm_fields = got
                except LLMError as exc:
                    warnings.append(f"LLM no disponible: {exc}")
            elif stated is not None and final.get("ValorFacturado") not in (None, "") \
                    and abs(float(final["ValorFacturado"]) - stated) > 0.5:
                warnings.append(f"el total del OCR ({float(final['ValorFacturado']):g}) difiere "
                                f"del TOTAL A PAGAR del texto ({stated:g})")

            warnings.extend(plausibility(final)[1])
            proposal.update(data={k: final.get(k) for k in REQUIRED}, campos_llm=llm_fields,
                            campos_regla=rule_fields, evidencias=evidence, avisos=warnings)
            errors = validate(proposal["data"], text, name_info, llm_fields, evidence)
            proposal["errores"] = errors
            if errors:
                proposal["status"] = "REVISAR"
            elif llm_fields:
                proposal["status"] = "LISTA_LLM"
            else:
                proposal["status"] = "LISTA"
            proposals.append(proposal)
            missing = _missing(proposal["data"])
            print(f"[{index}] {item.get('name')} -> {proposal['status']} "
                  f"(llm={len(llm_fields)} regla={len(rule_fields)}"
                  + (f" faltan={len(missing)}" if missing else "") + ")"
                  + (f" errores={len(errors)}" if errors else ""))
        except Exception as exc:
            logger.exception("Error proponiendo %s", item.get("id"))
            proposal.update(status="ERROR", errores=[f"{type(exc).__name__}: {exc}"])
            proposals.append(proposal)

    return proposals


def summarize(proposals: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for proposal in proposals:
        counts[proposal["status"]] = counts.get(proposal["status"], 0) + 1
    return counts


def write_proposals(proposals: list[dict], output: Path | None = None) -> Path:
    path = Path(output) if output else OUTPUT / f"pending_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "resumen": summarize(proposals),
        "propuestas": proposals,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


@contextmanager
def exclusive_apply_lock(path: Path):
    """Previene dos ejecuciones locales simultáneas de --apply.

    Si el proceso muere de forma abrupta el bloqueo permanece hasta que
    el operador revise el JSONL y compruebe que no hay otro proceso activo.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} timestamp={datetime.now().isoformat()}\n")
        yield
    finally:
        path.unlink(missing_ok=True)


def registered_ids(sheets, sheet_name: str) -> set[str]:
    return {str(row.get("ID") or "").strip() for row in sheets.get_rows(sheet_name)}


def apply_proposals(proposals: list[dict], log_path: Path, *, drive, sheets, settings,
                    to_01d: bool = False, require_review: bool = False) -> dict:
    """Mueve y registra solo lo que vuelve a validar. Devuelve el resumen."""
    known = {invoice_key(row.get("Cuenta"), row.get("N° Factura", row.get("NumeroFactura")))
             for row in sheets.get_rows(settings.sheet_bd_energy)}
    ids = registered_ids(sheets, settings.sheet_bd_energy)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {"aplicadas": 0, "duplicadas": 0, "omitidas": 0, "no_energia": 0}

    def log(entry: dict) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    for proposal in proposals:
        status = proposal.get("status")
        original = (proposal.get("parents") or [None])[0]
        if require_review and status in {"LISTA", "LISTA_LLM", "NO_ENERGIA"}:
            from pipeline.approval import has_valid_approval
            if not has_valid_approval(proposal):
                summary["omitidas"] += 1
                log({"id": proposal.get("id"), "accion": "SIN_APROBACION_MANUAL",
                     "motivo": "revisar PDF y aprobar con pipeline.approval"})
                continue
        if status == "NO_ENERGIA":
            if not to_01d:
                summary["omitidas"] += 1
                continue
            drive.move(proposal["id"], settings.drive_01d)
            log({"id": proposal["id"], "name": proposal.get("name"), "accion": "MOVER_01D",
                 "origen": original, "destino": settings.drive_01d,
                 "timestamp": datetime.now().isoformat(timespec="seconds")})
            summary["no_energia"] += 1
            continue

        if status not in {"LISTA", "LISTA_LLM"}:
            summary["omitidas"] += 1
            continue

        data = dict(proposal["data"])
        data["ID"] = proposal["id"]
        data["LinkFactura"] = f"https://drive.google.com/file/d/{proposal['id']}/view"
        errors = validate(data, proposal.get("texto", ""), proposal.get("nombre"),
                          proposal.get("campos_llm"), proposal.get("evidencias"))
        if errors:
            summary["omitidas"] += 1
            log({"id": proposal["id"], "accion": "OMITIDA", "errores": errors,
                 "timestamp": datetime.now().isoformat(timespec="seconds")})
            continue

        # Una propuesta no autoriza cambios si el original se movió o renombró.
        if hasattr(drive, "get_metadata"):
            try:
                current = drive.get_metadata(proposal["id"])
                if ((original and original not in current.get("parents", []))
                        or current.get("name") != proposal.get("name")):
                    summary["omitidas"] += 1
                    log({"id": proposal["id"], "accion": "DESACTUALIZADA",
                         "motivo": "cambió nombre o carpeta desde la propuesta"})
                    continue
                # La carpeta/nombre no prueban integridad: el documento puede
                # cambiar conservando el mismo Drive ID.
                if hasattr(drive, "download"):
                    expected = proposal.get("pdf_sha256")
                    if not expected:
                        summary["omitidas"] += 1
                        log({"id": proposal["id"], "accion": "SIN_HUELLA",
                             "motivo": "regenerar propuesta para registrar SHA-256"})
                        continue
                    real = hashlib.sha256(drive.download(proposal["id"])).hexdigest()
                    if real != expected:
                        summary["omitidas"] += 1
                        log({"id": proposal["id"], "accion": "PDF_CAMBIADO",
                             "motivo": "SHA-256 del PDF ya no coincide"})
                        continue
            except Exception as exc:
                summary["omitidas"] += 1
                log({"id": proposal["id"], "accion": "ERROR_META",
                     "error": type(exc).__name__})
                continue

        key = invoice_key(data["Cuenta"], data["NumeroFactura"])
        if key in known or proposal["id"] in ids:
            summary["duplicadas"] += 1
            log({"id": proposal["id"], "accion": "DUPLICADA", "cuenta": data["Cuenta"],
                 "factura": data["NumeroFactura"],
                 "timestamp": datetime.now().isoformat(timespec="seconds")})
            continue

        notes = []
        if proposal.get("campos_llm"):
            notes.append("Completado por LLM y validado: " + ", ".join(proposal["campos_llm"]))
        if proposal.get("campos_regla"):
            notes.append("Total tomado del TOTAL A PAGAR: " + ", ".join(proposal["campos_regla"]))
        if proposal.get("local_observaciones"):
            notes.append(str(proposal["local_observaciones"]))
        data["Observaciones"] = " | ".join(notes) or None

        folder = None
        moved = False
        append_attempted = False
        try:
            folder = drive.ensure_folder(settings.drive_02_energy,
                                         safe_filename_piece(data["Oficina"]))
            log({"id": proposal["id"], "accion": "PREPARADA", "origen": original,
                 "destino": folder["id"], "nombre": proposal.get("name")})
            drive.move(proposal["id"], folder["id"])
            moved = True
            drive.rename(proposal["id"], (
                f"{safe_filename_piece(data['Oficina'])} - {safe_filename_piece(data['Cuenta'])} - "
                f"{safe_filename_piece(data['Mes'])}/{safe_filename_piece(data['Año'])}.pdf"))
            append_attempted = True
            sheets.append(settings.sheet_bd_energy, _row(data), headers=ENERGY_HEADERS)
        except Exception as exc:
            # Ante timeout de Sheets no se sabe si la fila se insertó.
            # Una respuesta fallida no autoriza deshacer a ciegas el movimiento.
            sheet_state = "no_intentada"
            if append_attempted:
                try:
                    current_rows = sheets.get_rows(settings.sheet_bd_energy)
                    matches = [row for row in current_rows
                               if str(row.get("ID") or "").strip() == proposal["id"]]
                    if len(matches) == 1 and invoice_key(
                        matches[0].get("Cuenta"), matches[0].get("N° Factura",
                        matches[0].get("NumeroFactura"))) == key:
                        known.add(key)
                        ids.add(proposal["id"])
                        summary["aplicadas"] += 1
                        log({"id": proposal["id"], "accion": "APLICADA_RECONCILIADA",
                             "origen": original, "destino": folder["id"],
                             "motivo": "Sheets confirmó escritura tras error de respuesta"})
                        continue
                    sheet_state = "ausente" if not matches else "conflicto"
                except Exception as audit_exc:
                    sheet_state = "desconocido"
            if sheet_state in {"desconocido", "conflicto"}:
                summary["omitidas"] += 1
                log({"id": proposal["id"], "accion": "INDETERMINADA",
                     "error": type(exc).__name__, "estado_sheet": sheet_state,
                     "origen": original, "destino": (folder or {}).get("id"),
                     "motivo": "Requiere conciliación manual; no restaurar a ciegas"})
                continue
            rollback = "no_necesario"
            if moved and original:
                try:
                    drive.move(proposal["id"], original)
                    drive.rename(proposal["id"], proposal["name"])
                    rollback = "restaurado"
                except Exception as compensation:
                    rollback = f"REVISAR_MANUALMENTE:{type(compensation).__name__}"
            summary["omitidas"] += 1
            log({"id": proposal["id"], "accion": "ERROR_APLICACION",
                 "error": type(exc).__name__, "rollback": rollback,
                 "estado_sheet": sheet_state,
                 "origen": original, "destino": (folder or {}).get("id")})
            continue
        known.add(key)
        ids.add(proposal["id"])
        log({"id": proposal["id"], "name": proposal.get("name"), "accion": "APLICADA",
             "origen": original, "destino": folder["id"], "cuenta": data["Cuenta"],
             "factura": data["NumeroFactura"], "campos_llm": proposal.get("campos_llm"),
             "campos_regla": proposal.get("campos_regla"),
             "timestamp": datetime.now().isoformat(timespec="seconds")})
        summary["aplicadas"] += 1

    return summary


def revalidate(proposals: list[dict]) -> list[dict]:
    """Recalifica un JSON ya generado sin volver a leer PDFs ni llamar al LLM."""
    for proposal in proposals:
        if proposal.get("status") in {"NO_ENERGIA", "ERROR"}:
            continue
        data = dict(proposal.get("data") or {})
        data["ID"] = proposal.get("id")
        errors = validate(data, proposal.get("texto", ""), proposal.get("nombre"),
                          proposal.get("campos_llm"), proposal.get("evidencias"))
        _, warnings = plausibility(data)
        avisos = [aviso for aviso in (proposal.get("avisos") or [])
                  if aviso not in warnings]
        proposal["avisos"] = avisos + [w for w in warnings if w not in avisos]
        proposal["errores"] = errors
        if errors:
            proposal["status"] = "REVISAR"
        elif proposal.get("campos_llm"):
            proposal["status"] = "LISTA_LLM"
        else:
            proposal["status"] = "LISTA"
    return proposals


def main() -> int:
    parser = argparse.ArgumentParser(description="Propone (y opcionalmente aplica) la revisión de 01C.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-llm", action="store_true", help="Solo OCR local")
    parser.add_argument("--folder", default=None, help="Carpeta de origen (por defecto 01C)")
    parser.add_argument("--apply", action="store_true", help="Aplica un archivo de propuestas ya revisado")
    parser.add_argument("--from", dest="source", default=None, help="JSON de propuestas a aplicar")
    parser.add_argument("--revalidate", action="store_true",
                        help="Recalifica un JSON ya generado sin volver a leer PDFs ni llamar al LLM")
    parser.add_argument("--to-01d", action="store_true", help="Mover NO_ENERGIA a 01D al aplicar")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")

    if args.revalidate:
        if not args.source:
            parser.error("--revalidate requiere --from con el JSON de propuestas")
        payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
        proposals = revalidate(payload.get("propuestas", payload))
        path = write_proposals(proposals, Path(args.output) if args.output else None)
        print("Resumen:", " | ".join(f"{k}={v}" for k, v in summarize(proposals).items()))
        print(f"Propuestas recalificadas: {path}")
        return 0

    if args.apply:
        if not args.source:
            parser.error("--apply requiere --from con el JSON de propuestas revisado")
        payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
        proposals = payload.get("propuestas", payload)
        settings = get_settings()
        drive = DriveService(settings.google_client_secret_file, settings.google_token_file)
        sheets = SheetsService(settings.google_client_secret_file, settings.google_token_file,
                               settings.spreadsheet_id)
        log_path = OUTPUT / f"applied_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        try:
            with exclusive_apply_lock(OUTPUT / "pending_apply.lock"):
                summary = apply_proposals(proposals, log_path, drive=drive, sheets=sheets,
                                          settings=settings, to_01d=args.to_01d,
                                          require_review=True)
        except FileExistsError:
            print("Bloqueo de aplicación activo. Revise procesos y bitácoras antes de quitarlo.")
            return 2
        print("Aplicación:", " | ".join(f"{k}={v}" for k, v in summary.items()))
        print(f"Bitácora: {log_path}")
        return 0

    proposals = propose(limit=args.limit, model=args.model, source_id=args.folder,
                        use_llm=not args.no_llm)
    path = write_proposals(proposals, Path(args.output) if args.output else None)
    print("\nResumen:", " | ".join(f"{k}={v}" for k, v in summarize(proposals).items()))
    print(f"Propuestas: {path}")
    print("Nada se movió ni se escribió. Revisa el JSON y aplica con --apply --from")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
