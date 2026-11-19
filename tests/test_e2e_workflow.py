"""Prueba integral simulada: Drive -> OCR -> Gemini -> JSON -> revisión -> Sheets."""
from dataclasses import dataclass

import pipeline.pending as pending
import pipeline.refresh as renew
from pipeline.energy import ENERGY_HEADERS
from pipeline.reconcile import review
from services.llm import LLMResult
from test_pending import DATA, NAME, TEXT


PDF = b"%PDF-1.4\ninvoice-input\n"


class Drive:
    def __init__(self):
        self.data = PDF
        self.name = NAME
        self.parent = "01c"
        self.moved = []

    def iter_pdfs(self, source):
        assert source == "01c"
        yield {"id": "invoice-1", "name": self.name, "parents": [self.parent]}

    def download(self, id):
        assert id == "invoice-1"
        return self.data

    def get_metadata(self, id):
        return {"name": self.name, "parents": [self.parent]}

    def ensure_folder(self, parent, name):
        return {"id": f"dest::{name}"}

    def move(self, id, folder):
        self.moved.append((id, folder))
        self.parent = folder

    def rename(self, id, name):
        self.name = name


class Sheet:
    def __init__(self):
        self.rows = []

    def get_rows(self, name):
        assert name == "BD_energia"
        return list(self.rows)

    def append(self, name, values, headers=None):
        assert headers == ENERGY_HEADERS
        self.rows.append(dict(zip(headers, values)))
        return {"updates": {"updatedRows": 1}}


@dataclass
class Settings:
    google_client_secret_file: str = "fake"
    google_token_file: str = "fake"
    drive_01c: str = "01c"
    drive_02_energy: str = "energy"
    drive_01d: str = "01d"
    sheet_bd_energy: str = "BD_energia"
    pdf_min_text_characters: int = 20


def test_e2e_happy_path_is_auditable_and_idempotent(monkeypatch, tmp_path):
    drive = Drive()
    sheet = Sheet()
    monkeypatch.setattr(pending, "get_settings", Settings)
    monkeypatch.setattr(pending, "DriveService", lambda *args: drive)
    monkeypatch.setattr(pending, "extract_text", lambda *args: TEXT)
    monkeypatch.setattr(renew, "extract_text", lambda *args: TEXT)
    monkeypatch.setattr(pending, "classify", lambda *args: "ENERGIA")
    monkeypatch.setattr(pending, "extract_invoice", lambda *args: {**DATA, "kWh": None})
    def fake_gemini(*args, **kwargs):
        return LLMResult({"kWh": 171.0,
                          "evidencias": [{"campo": "kwh", "cita": "CONSUMO MES: 171 kWh"}]},
                         "gemini-simulado", 0.01)
    monkeypatch.setattr(pending, "llm_extract", fake_gemini)
    entries = pending.propose(limit=1)
    assert len(entries) == 1
    candidate = entries[0]
    assert candidate["status"] == "LISTA_LLM"
    assert candidate["pdf_sha256"]

    refreshed, report = renew.refresh(entries, drive, 20)
    assert report["verificadas"] == 1
    audit = review(refreshed, sheet.get_rows("BD_energia"), drive, verify_pdf=True)
    assert audit["resumen"] == {"VERIFICADA": 1}

    outcome = pending.apply_proposals(refreshed, tmp_path / "applied.jsonl",
                                      drive=drive, sheets=sheet, settings=Settings())
    assert outcome["aplicadas"] == 1 and len(sheet.rows) == 1
    assert sheet.rows[0]["kWh"] == 171.0
    assert sheet.rows[0]["Cuenta"] == DATA["Cuenta"]
    assert drive.parent.startswith("dest::")
    assert len(drive.moved) == 1

    again = pending.apply_proposals(refreshed, tmp_path / "retry.jsonl",
                                    drive=drive, sheets=sheet, settings=Settings())
    assert again["aplicadas"] == 0 and len(sheet.rows) == 1
    assert len(drive.moved) == 1
