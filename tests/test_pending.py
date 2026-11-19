from types import SimpleNamespace

from pipeline.pending import (apply_proposals, merge_fields, parse_pending_name,
                              validate)

NAME = "ENERGIA_PENDIENTE - APTO ARRIENDO - 21556058 - 05-2025.pdf"

TEXT = """NÚMERO DE CLIENTE 2155605-8
Documento Equivalente Electrónico No. 354585072-3
CONSUMO MES: 171 kWh
PERIODO FACTURADO: 29 ABR/2025 A 28 MAY/2025
TOTAL A PAGAR
$142.110
"""

DATA = {
    "Oficina": "APTO ARRIENDO",
    "Cuenta": "21556058",
    "NumeroFactura": "354585072-3",
    "kWh": 171.0,
    "ValorFacturado": 142110.0,
    "InicioPeriodo": "2025-04-29",
    "FinPeriodo": "2025-05-28",
    "Año": 2025,
    "Mes": "05",
}


def settings(**overrides):
    base = dict(drive_02_energy="energy", drive_01c="pending", drive_01d="no-procesables",
                sheet_bd_energy="bd")
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeDrive:
    def __init__(self):
        self.moved, self.renamed = [], []

    def ensure_folder(self, parent_id, name):
        return {"id": f"folder::{name}"}

    def move(self, file_id, folder_id):
        self.moved.append((file_id, folder_id))

    def rename(self, file_id, name):
        self.renamed.append((file_id, name))


class FakeSheets:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.written = []

    def get_rows(self, sheet_name):
        return self.rows

    def append(self, sheet_name, values, headers=None):
        self.written.append((sheet_name, values))
        return {"updates": {"updatedRows": 1}}


def proposal(**overrides):
    entry = {
        "id": "file-1",
        "name": NAME,
        "parents": ["01c"],
        "status": "LISTA",
        "texto": TEXT,
        "nombre": {"Oficina": "APTO ARRIENDO", "Cuenta": "21556058",
                   "Mes": "05", "Año": 2025},
        "data": dict(DATA),
        "campos_llm": [],
        "campos_regla": [],
        "evidencias": [],
        "local_observaciones": None,
    }
    entry.update(overrides)
    return entry


def test_parse_pending_name_reads_the_pipeline_stamp():
    info = parse_pending_name(NAME)
    assert info == {"Oficina": "APTO ARRIENDO", "Cuenta": "21556058",
                    "Mes": "05", "Año": 2025}


def test_parse_pending_name_zero_pads_the_month():
    info = parse_pending_name("ENERGIA_PENDIENTE - BODEGAS - 04527753 - 6-2025.pdf")
    assert info["Mes"] == "06" and info["Año"] == 2025


def test_parse_pending_name_ignores_free_text_files():
    assert parse_pending_name("factura.pdf") is None


def test_merge_fields_never_overwrites_local_values():
    local = {"Cuenta": "21556058", "kWh": None}
    final, used = merge_fields(local, {"Cuenta": "99999999", "kWh": 171.0,
                                       "ValorFacturado": 142110.0})
    assert final["Cuenta"] == "21556058"
    assert final["kWh"] == 171.0
    assert used == ["kWh", "ValorFacturado"]


def test_validate_accepts_a_complete_row_that_matches_the_name_and_text():
    assert validate(dict(DATA), TEXT, parse_pending_name(NAME)) == []


def test_validate_reports_missing_fields_first():
    data = dict(DATA, NumeroFactura="")
    assert validate(data, TEXT, parse_pending_name(NAME))[0].startswith("faltan campos")


def test_validate_blocks_an_account_that_contradicts_the_file_name():
    data = dict(DATA, Cuenta="04676007")
    errors = validate(data, TEXT, parse_pending_name(NAME))
    assert any("no coincide con el nombre" in error for error in errors)


def test_validate_blocks_a_period_that_contradicts_the_file_name():
    data = dict(DATA, Mes="06")
    errors = validate(data, TEXT, parse_pending_name(NAME))
    assert any("período" in error for error in errors)


def test_validate_rejects_the_polluted_total_only_when_it_came_from_the_llm():
    data = dict(DATA, ValorFacturado=430930.0)
    assert validate(data, TEXT, None, ["ValorFacturado"],
                    [{"campo": "valor_facturado", "cita": "TOTAL ENERGÍA $430.930"}])
    assert validate(data, TEXT, None) == []


def test_validate_requires_literal_evidence_for_every_llm_field():
    errors = validate(dict(DATA), TEXT, None, ["NumeroFactura"], [])
    assert errors == ["sin evidencia literal para NumeroFactura"]


def test_validate_accepts_evidence_named_like_the_prompt():
    evidence = [{"campo": "factura", "cita": "Documento Equivalente Electrónico No. 354585072-3"}]
    assert validate(dict(DATA), TEXT, None, ["NumeroFactura"], evidence) == []


def test_validate_rejects_an_inverted_period():
    data = dict(DATA, InicioPeriodo="2025-05-28", FinPeriodo="2025-04-29")
    assert "período invertido" in validate(data, TEXT, None)


def test_apply_moves_registers_and_logs_a_valid_proposal(tmp_path):
    drive, sheets = FakeDrive(), FakeSheets()
    log = tmp_path / "applied.jsonl"
    summary = apply_proposals([proposal()], log, drive=drive, sheets=sheets,
                              settings=settings())
    assert summary == {"aplicadas": 1, "duplicadas": 0, "omitidas": 0, "no_energia": 0}
    assert drive.moved == [("file-1", "folder::APTO ARRIENDO")]
    assert drive.renamed == [("file-1", "APTO ARRIENDO - 21556058 - 05/2025.pdf")]
    assert sheets.written[0][0] == "bd"
    assert sheets.written[0][1][12] == 142110.0
    actions = [__import__("json").loads(line)["accion"]
               for line in log.read_text(encoding="utf-8").splitlines()]
    assert actions == ["PREPARADA", "APLICADA"]


def test_apply_skips_a_proposal_that_no_longer_validates(tmp_path):
    drive, sheets = FakeDrive(), FakeSheets()
    tampered = proposal(data=dict(DATA, Cuenta="00000000"))
    summary = apply_proposals([tampered], tmp_path / "log.jsonl", drive=drive,
                              sheets=sheets, settings=settings())
    assert summary["omitidas"] == 1 and summary["aplicadas"] == 0
    assert drive.moved == [] and sheets.written == []


def test_apply_does_not_duplicate_an_invoice_already_in_the_sheet(tmp_path):
    rows = [{"ID": "otro", "Cuenta": "21556058", "N° Factura": "354585072-3"}]
    drive, sheets = FakeDrive(), FakeSheets(rows)
    summary = apply_proposals([proposal()], tmp_path / "log.jsonl", drive=drive,
                              sheets=sheets, settings=settings())
    assert summary["duplicadas"] == 1
    assert drive.moved == [] and sheets.written == []


def test_apply_leaves_non_energy_files_alone_unless_asked(tmp_path):
    entry = proposal(status="NO_ENERGIA", data={})
    drive, sheets = FakeDrive(), FakeSheets()
    summary = apply_proposals([entry], tmp_path / "log.jsonl", drive=drive,
                              sheets=sheets, settings=settings())
    assert summary["omitidas"] == 1 and drive.moved == []

    summary = apply_proposals([entry], tmp_path / "log2.jsonl", drive=drive,
                              sheets=sheets, settings=settings(), to_01d=True)
    assert summary["no_energia"] == 1
    assert drive.moved == [("file-1", "no-procesables")]


def test_apply_records_who_completed_the_row(tmp_path):
    drive, sheets = FakeDrive(), FakeSheets()
    entry = proposal(campos_llm=["ValorFacturado"], campos_regla=[],
                     evidencias=[{"campo": "valor_facturado", "cita": "TOTAL A PAGAR $142.110"}],
                     local_observaciones="No se pudieron validar con reglas: kWh")
    apply_proposals([entry], tmp_path / "log.jsonl", drive=drive, sheets=sheets,
                    settings=settings())
    notes = sheets.written[0][1][11]
    assert "Completado por LLM y validado: ValorFacturado" in notes
    assert "No se pudieron validar con reglas" in notes


def test_propose_asks_the_llm_only_for_the_fields_the_ocr_left_empty(monkeypatch):
    from types import SimpleNamespace

    import pipeline.pending as pending
    from services.llm import LLMResult

    monkeypatch.setattr(pending, "get_settings", lambda: SimpleNamespace(
        google_client_secret_file="c.json", google_token_file="t.json",
        drive_01c="01c", pdf_min_text_characters=20))
    monkeypatch.setattr(pending, "DriveService", lambda *a, **k: SimpleNamespace(
        iter_pdfs=lambda source: [{"id": "file-1", "name": NAME, "parents": ["01c"]}],
        download=lambda file_id: b"%PDF-1.4"))
    monkeypatch.setattr(pending, "extract_text", lambda pdf, minimum: TEXT)
    monkeypatch.setattr(pending, "classify", lambda name, text: "ENERGIA")
    monkeypatch.setattr(pending, "extract_invoice", lambda text: dict(DATA, kWh=None,
                                                                    ValorFacturado=None))
    calls = []

    def fake_llm(pdf, model=None):
        calls.append(model)
        return LLMResult(
            {"Cuenta": "", "NumeroFactura": "", "kWh": 171.0, "ValorFacturado": None,
             "InicioPeriodo": None, "FinPeriodo": None, "evidencias": [
                 {"campo": "kwh", "cita": "CONSUMO MES: 171 kWh"}]},
            "gemini-fake", 1.0, warnings=[])

    monkeypatch.setattr(pending, "llm_extract", fake_llm)

    proposal = pending.propose(limit=1)[0]

    assert len(calls) == 1
    assert proposal["status"] == "LISTA_LLM"
    # El total lo puso la regla antes de llamar al LLM; al modelo solo se le pidió kWh.
    assert proposal["campos_llm"] == ["kWh"]
    assert proposal["campos_regla"] == ["ValorFacturado"]
    assert proposal["data"]["Oficina"] == "APTO ARRIENDO"
    assert proposal["errores"] == []


def test_propose_prefers_the_document_total_over_the_llm(monkeypatch):
    from types import SimpleNamespace

    import pipeline.pending as pending
    from services.llm import LLMResult

    monkeypatch.setattr(pending, "get_settings", lambda: SimpleNamespace(
        google_client_secret_file="c.json", google_token_file="t.json",
        drive_01c="01c", pdf_min_text_characters=20))
    monkeypatch.setattr(pending, "DriveService", lambda *a, **k: SimpleNamespace(
        iter_pdfs=lambda source: [{"id": "file-1", "name": NAME, "parents": ["01c"]}],
        download=lambda file_id: b"%PDF-1.4"))
    monkeypatch.setattr(pending, "extract_text", lambda pdf, minimum: TEXT)
    monkeypatch.setattr(pending, "classify", lambda name, text: "ENERGIA")
    monkeypatch.setattr(pending, "extract_invoice", lambda text: dict(DATA, kWh=None,
                                                                    ValorFacturado=None))
    # El error real del benchmark: el modelo toma TOTAL ENERGÍA, no TOTAL A PAGAR.
    monkeypatch.setattr(pending, "llm_extract", lambda pdf, model=None: LLMResult(
        dict(DATA, ValorFacturado=430930.0, Oficina=None), "gemini-fake", 1.0,
        warnings=[]))

    proposal = pending.propose(limit=1)[0]

    assert proposal["data"]["ValorFacturado"] == 142110.0
    assert "ValorFacturado" not in proposal["campos_llm"]
    assert proposal["campos_regla"] == ["ValorFacturado"]
    assert any("TOTAL A PAGAR" in aviso for aviso in proposal["avisos"])


def test_propose_survives_an_unavailable_llm_and_says_why(monkeypatch):
    from types import SimpleNamespace

    import pipeline.pending as pending
    from services.llm import LLMResult

    monkeypatch.setattr(pending, "get_settings", lambda: SimpleNamespace(
        google_client_secret_file="c.json", google_token_file="t.json",
        drive_01c="01c", pdf_min_text_characters=20))
    monkeypatch.setattr(pending, "DriveService", lambda *a, **k: SimpleNamespace(
        iter_pdfs=lambda source: [{"id": "file-1", "name": NAME, "parents": ["01c"]}],
        download=lambda file_id: b"%PDF-1.4"))
    monkeypatch.setattr(pending, "extract_text", lambda pdf, minimum: TEXT)
    monkeypatch.setattr(pending, "classify", lambda name, text: "ENERGIA")
    monkeypatch.setattr(pending, "extract_invoice", lambda text: dict(DATA, kWh=None))

    def broken(pdf, model=None):
        raise pending.LLMError("HTTP 503")

    monkeypatch.setattr(pending, "llm_extract", broken)

    proposal = pending.propose(limit=1)[0]

    assert proposal["status"] == "REVISAR"
    assert any("LLM no disponible" in aviso for aviso in proposal["avisos"])
    assert proposal["errores"] == ["faltan campos: kWh"]


def test_validate_blocks_a_negative_total_that_is_really_a_credit():
    data = dict(DATA, ValorFacturado=-15.0, kWh=0.0)
    errors = validate(data, TEXT, None)
    assert any("total negativo" in error for error in errors)


def test_validate_only_accepts_zero_when_the_document_declares_it():
    zero = dict(DATA, ValorFacturado=0.0, kWh=0.0)
    assert validate(zero, "FACTURA SIN COBRO", None) == []
    assert validate(zero, TEXT.replace("TOTAL A PAGAR\n$142.110", "TOTAL A PAGAR\n$0"), None) == []
    assert any("total en cero" in error for error in validate(zero, TEXT, None))


def test_validate_blocks_a_total_that_cannot_be_energy_at_that_consumption():
    # Caso real: un "8" espurio del OCR convirtió 18.779.520 en 818.779.520.
    data = dict(DATA, ValorFacturado=818779520.0, kWh=19060.0)
    errors = validate(data, TEXT, None)
    assert any("COP/kWh" in error for error in errors)


def test_plausibility_warns_without_blocking_when_the_price_is_odd():
    from pipeline.pending import plausibility
    errors, warnings = plausibility(dict(DATA, ValorFacturado=250020.0, kWh=0.0))
    assert errors == [] and any("consumo en cero" in aviso for aviso in warnings)
    errors, warnings = plausibility(dict(DATA, ValorFacturado=2_451_390.0, kWh=15038.0))
    assert errors == [] and any("COP/kWh" in aviso for aviso in warnings)


def test_revalidate_demotes_a_proposal_that_no_longer_holds():
    from pipeline.pending import revalidate

    entry = proposal(status="LISTA_LLM", campos_llm=["ValorFacturado"],
                     data=dict(DATA, ValorFacturado=-15.0, kWh=0.0))
    result = revalidate([entry])[0]
    assert result["status"] == "REVISAR"
    assert any("total negativo" in error for error in result["errores"])
