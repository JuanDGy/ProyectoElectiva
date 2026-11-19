from types import SimpleNamespace

from pipeline.energy import invoice_key, process_energy


def test_energy_pipeline_archives_and_records_complete_invoice_without_external_ai():
    written = []
    moved = []
    class Drive:
        def download(self, file_id):
            return b'PDF'
        def ensure_folder(self, parent_id, name):
            return {'id': 'office-folder'}
        def move(self, file_id, folder_id):
            moved.append((file_id, folder_id))
        def rename(self, file_id, name):
            pass
    class Sheets:
        def append(self, sheet_name, values, headers=None):
            written.append((sheet_name, values))
    settings = SimpleNamespace(drive_02_energy='energy',
                               sheet_bd_energy='bd', sheet_adjust_energy='adjust', drive_01c='pending')
    text = '''Número de cliente: 0326876-6
    Factura No. 355379219-1
    Consumo mes: 100 kWh
    Periodo facturado: 04 MAY/2025 A 04 JUN/2025
    Total a pagar: $125.000'''
    result = process_energy({'id': 'invoice', 'ocrText': text, 'sourceOffice': 'Principal'},
                            Drive(), Sheets(), settings)
    assert result['status'] == 'PROCESADA'
    assert moved == [('invoice', 'office-folder')]
    assert written[0][0] == 'bd'
    assert written[0][1][3] == '03268766'
    assert written[0][1][5] == 100


TEXT = '''Número de cliente: 0326876-6
    Factura No. 355379219-1
    Consumo mes: 100 kWh
    Periodo facturado: 04 MAY/2025 A 04 JUN/2025
    Total a pagar: $125.000'''


def settings():
    return SimpleNamespace(drive_02_energy='energy', sheet_bd_energy='bd',
                           sheet_adjust_energy='adjust', drive_01c='pending')


class SilentDrive:
    moved = []
    renamed = []

    def ensure_folder(self, parent_id, name):
        return {'id': 'office-folder'}

    def move(self, file_id, folder_id):
        self.moved.append((file_id, folder_id))

    def rename(self, file_id, name):
        self.renamed.append((file_id, name))


def test_history_mode_writes_without_moving_or_renaming_the_original():
    written = []

    class Sheets:
        def append(self, sheet_name, values, headers=None):
            written.append(sheet_name)

    drive = SilentDrive()
    result = process_energy({'id': 'history', 'ocrText': TEXT, 'sourceOffice': 'Principal'},
                            drive, Sheets(), settings(), archive=False)
    assert result['status'] == 'PROCESADA'
    assert written == ['bd']
    assert drive.moved == [] and drive.renamed == []


def test_invoice_already_in_sheet_is_not_duplicated():
    written = []

    class Sheets:
        def append(self, sheet_name, values, headers=None):
            written.append(sheet_name)

    known = {invoice_key('0326876-6', '355379219-1')}
    drive = SilentDrive()
    result = process_energy({'id': 'again', 'ocrText': TEXT, 'sourceOffice': 'Principal'},
                            drive, Sheets(), settings(), known=known)
    assert result['status'] == 'DUPLICADA'
    assert written == [] and drive.moved == []


def test_processing_adds_the_invoice_to_the_known_set():
    known = set()
    process_energy({'id': 'new', 'ocrText': TEXT, 'sourceOffice': 'Principal'},
                   SilentDrive(), SimpleNamespace(append=lambda *a, **k: None), settings(), known=known)
    assert known == {invoice_key('03268766', '3553792191')}
