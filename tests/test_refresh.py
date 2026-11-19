"""Tests de renovación de propuestas: solo lectura, no usa LLM."""
import hashlib

import pipeline.refresh as module
from test_pending import NAME, TEXT, proposal


class StubDrive:
    def __init__(self, pdf=b"PDF", name=NAME, parents=("01c",)):
        self.pdf = pdf
        self.name = name
        self.parents = list(parents)
        self.read_count = 0
        self.writes = 0

    def get_metadata(self, id):
        return {"name": self.name, "parents": self.parents}

    def download(self, id):
        self.read_count += 1
        return self.pdf


def test_refresh_adds_hash_only_when_current_ocr_matches(monkeypatch):
    monkeypatch.setattr(module, "extract_text", lambda pdf, min_chars: TEXT)
    d = StubDrive()
    rows, info = module.refresh([proposal()], d, 10)
    assert info["verificadas"] == 1
    assert rows[0]["pdf_sha256"] == hashlib.sha256(d.pdf).hexdigest()
    assert rows[0]["status"] == "LISTA" and d.writes == 0


def test_refresh_marks_changed_text_for_review(monkeypatch):
    monkeypatch.setattr(module, "extract_text", lambda pdf, min_chars: "Contenido cambiado")
    d = StubDrive()
    rows, info = module.refresh([proposal()], d, 10)
    assert info["texto_diferente"] == 1
    assert rows[0]["status"] == "REVISAR" and "pdf_sha256" not in rows[0]


def test_refresh_detects_changed_location_without_download(monkeypatch):
    monkeypatch.setattr(module, "extract_text", lambda pdf, min_chars: TEXT)
    d = StubDrive(parents=["otro"])
    rows, info = module.refresh([proposal()], d, 10)
    assert info["movidas"] == 1 and d.read_count == 0
    assert rows[0]["status"] == "REVISAR"


def test_refresh_limit_and_invalid_do_not_query_llm(monkeypatch):
    monkeypatch.setattr(module, "extract_text", lambda pdf, min_chars: TEXT)
    d = StubDrive()
    rows, info = module.refresh([proposal(), proposal(id="file-2")], d, 10, limit=1)
    assert info["verificadas"] == 1 and info["fuera_de_limite"] == 1
    assert d.read_count == 1
    invalid = proposal(data={"Cuenta": "0000"})
    rows, info = module.refresh([invalid], d, 10)
    assert info["invalidas"] == 1
    assert d.read_count == 1
