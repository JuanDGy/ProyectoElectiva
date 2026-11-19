"""Verificación de integridad, conciliación y fallos distribuidos sin usar la nube."""
import hashlib
import json

from pipeline.pending import apply_proposals
from pipeline.reconcile import review
from test_pending import FakeDrive, FakeSheets, NAME, proposal, settings


PDF = b"%PDF-1.4\nfactura simulada\n"


class VerifiedDrive(FakeDrive):
    def __init__(self, document=PDF, name=NAME, parent="01c"):
        super().__init__()
        self.document = document
        self.name = name
        self.parent = parent

    def get_metadata(self, file_id):
        return {"id": file_id, "name": self.name, "parents": [self.parent]}

    def download(self, file_id):
        return self.document

    def move(self, file_id, folder_id):
        super().move(file_id, folder_id)
        self.parent = folder_id

    def rename(self, file_id, name):
        super().rename(file_id, name)
        self.name = name


def signed_proposal(**kwargs):
    return proposal(pdf_sha256=hashlib.sha256(PDF).hexdigest(), **kwargs)


def test_readonly_audit_detects_old_unfingerprinted_proposals():
    result = review([proposal()])
    assert result["resumen"] == {"REVISAR": 1}
    assert "sin huella" in result["documentos"][0]["motivos"][0]


def test_readonly_audit_fingerprinted_pdf_is_verified():
    drive = VerifiedDrive()
    result = review([signed_proposal()], [], drive, verify_pdf=True)
    assert result["resumen"] == {"VERIFICADA": 1}
    assert drive.moved == [] and drive.renamed == []


def test_readonly_audit_detects_changed_content_and_duplicate():
    drive = VerifiedDrive(document=b"cambiado")
    result = review([signed_proposal()], [], drive, verify_pdf=True)
    assert result["resumen"] == {"REVISAR": 1}
    assert any("SHA-256" in reason for reason in result["documentos"][0]["motivos"])
    already = [{"ID": "file-1", "Cuenta": "21556058", "N° Factura": "354585072-3"}]
    result = review([signed_proposal()], already)
    assert result["resumen"] == {"REVISAR": 1}
    assert any("ya registrada" in reason for reason in result["documentos"][0]["motivos"])


def test_apply_rejects_changed_pdf_even_if_name_and_parent_unchanged(tmp_path):
    drive, sheet = VerifiedDrive(document=b"different contents"), FakeSheets()
    result = apply_proposals([signed_proposal()], tmp_path / "audit.jsonl",
                             drive=drive, sheets=sheet, settings=settings())
    assert result["omitidas"] == 1
    assert not drive.moved and not sheet.written
    assert "PDF_CAMBIADO" in (tmp_path / "audit.jsonl").read_text()


def test_apply_rejects_legacy_proposal_without_sha(tmp_path):
    drive, sheet = VerifiedDrive(), FakeSheets()
    result = apply_proposals([proposal()], tmp_path / "audit.jsonl",
                             drive=drive, sheets=sheet, settings=settings())
    assert result["omitidas"] == 1
    assert not drive.moved and not sheet.written
    assert "SIN_HUELLA" in (tmp_path / "audit.jsonl").read_text()


def test_apply_valid_new_proposal(tmp_path):
    drive, sheet = VerifiedDrive(), FakeSheets()
    result = apply_proposals([signed_proposal()], tmp_path / "audit.jsonl",
                             drive=drive, sheets=sheet, settings=settings())
    assert result["aplicadas"] == 1 and result["omitidas"] == 0
    assert len(drive.moved) == 1 and len(sheet.written) == 1


def test_sheet_timeout_after_insert_is_reconciled_without_rollback(tmp_path):
    class SheetSavedThenTimeout(FakeSheets):
        def append(self, name, values, headers=None):
            keys = ["ID", "Código", "Oficina", "Cuenta", "N° Factura", "kWh", "Año",
                    "Mes", "Inicio periodo", "Fin periodo", "Link factura",
                    "Observaciones", "ValorFacturado"]
            self.rows.append(dict(zip(keys, values)))
            raise TimeoutError("response lost")

    drive, sheet = VerifiedDrive(), SheetSavedThenTimeout()
    log = tmp_path / "audit.jsonl"
    result = apply_proposals([signed_proposal()], log,
                             drive=drive, sheets=sheet, settings=settings())
    assert result["aplicadas"] == 1 and result["omitidas"] == 0
    assert len(drive.moved) == 1
    assert "APLICADA_RECONCILIADA" in log.read_text()


def test_sheet_unknown_outcome_is_not_rolled_back(tmp_path):
    class SheetUncertain(FakeSheets):
        def __init__(self):
            super().__init__()
            self.failed = False
        def append(self, *args, **kwargs):
            self.failed = True
            raise ConnectionError("unknown outcome")
        def get_rows(self, name):
            if self.failed:
                raise ConnectionError("cannot verify")
            return super().get_rows(name)

    drive, sheet = VerifiedDrive(), SheetUncertain()
    log = tmp_path / "audit.jsonl"
    result = apply_proposals([signed_proposal()], log,
                             drive=drive, sheets=sheet, settings=settings())
    assert result["omitidas"] == 1
    assert len(drive.moved) == 1  # No retroceder sin comprobar Sheets
    audit = [json.loads(line) for line in log.read_text().splitlines()]
    assert audit[-1]["accion"] == "INDETERMINADA"
