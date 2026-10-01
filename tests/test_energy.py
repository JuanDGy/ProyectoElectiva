from types import SimpleNamespace

from pipeline.energy import process_energy


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
