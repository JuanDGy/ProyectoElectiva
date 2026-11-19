from types import SimpleNamespace

import main
from pipeline.energy import invoice_key


def test_registered_invoices_normalizes_account_and_invoice():
    class Sheets:
        def get_rows(self, sheet_name):
            return [
                {"Cuenta": "0326876-6", "N° Factura": "355379219-1"},
                {"Cuenta": "04525220", "N° Factura": "3433361437"},
            ]

    settings = SimpleNamespace(sheet_bd_energy="bd")
    assert main.registered_invoices(Sheets(), settings) == {
        ("03268766", "3553792191"),
        ("04525220", "3433361437"),
    }


def test_history_manifest_roundtrip_ignores_broken_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    assert main.history_done_ids(path) == set()

    main.record_history({"id": "a", "status": "PROCESADA"}, path)
    main.record_history({"id": "b", "status": "DUPLICADA"}, path)

    with path.open("a", encoding="utf-8") as handle:
        handle.write("{roto\n")

    assert main.history_done_ids(path) == {"a", "b"}


def test_run_history_never_moves_files_and_skips_duplicates(monkeypatch, tmp_path):
    manifest = tmp_path / "history.jsonl"
    monkeypatch.setattr(main, "HISTORY_MANIFEST", manifest)

    settings = SimpleNamespace(
        log_level="CRITICAL",
        google_client_secret_file="c.json",
        google_token_file="t.json",
        spreadsheet_id="sheet",
        drive_history_root_id="root",
        sheet_bd_energy="bd",
        pdf_min_text_characters=100,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    moved, renamed, written = [], [], []

    class Drive:
        def iter_pdfs(self, root_id):
            yield {"id": "f1", "name": "uno.pdf", "parents": ["root"]}
            yield {"id": "f2", "name": "dos.pdf", "parents": ["root"]}
            yield {"id": "f3", "name": "tres.pdf", "parents": ["root"]}

        def download(self, file_id):
            return b"PDF"

        def source_office(self, metadata, root_id):
            return "BIBLIOTECA"

        def move(self, file_id, folder_id):
            moved.append((file_id, folder_id))

        def rename(self, file_id, name):
            renamed.append((file_id, name))

        def ensure_folder(self, parent_id, name):
            return {"id": "office"}

    class Sheets:
        def get_rows(self, sheet_name):
            return [{"Cuenta": "0326876-6", "N° Factura": "355379219-1"}]

        def append(self, sheet_name, values, headers=None):
            written.append(values)

    monkeypatch.setattr(main, "DriveService", lambda *a, **k: Drive())
    monkeypatch.setattr(main, "SheetsService", lambda *a, **k: Sheets())
    monkeypatch.setattr(main, "extract_text", lambda pdf, minimum: "texto")
    monkeypatch.setattr(main, "classify", lambda name, text: "ENERGIA")

    # f1 ya está en la hoja: no se escribe. f2 es nuevo. f3 ya se trató antes.
    monkeypatch.setattr(main, "process_energy", _fake_process)
    main.record_history({"id": "f3", "status": "NO_ENERGIA"}, manifest)

    assert main.run_history() == 0
    assert moved == [] and renamed == []
    assert [row["id"] for row in written] == ["f2"]
    assert main.history_done_ids(manifest) == {"f1", "f2", "f3"}


def _fake_process(ctx, drive, sheets, settings, archive=True, known=None):
    factura = {"f1": "355379219-1", "f2": "999", "f3": "111"}[ctx["id"]]
    key = invoice_key("03268766", factura)
    data = {"Cuenta": "03268766", "NumeroFactura": factura}
    if key in known:
        return {"status": "DUPLICADA", "data": data}
    sheets.append("bd", {"id": ctx["id"], "factura": factura})
    known.add(key)
    return {"status": "PROCESADA", "data": data}
