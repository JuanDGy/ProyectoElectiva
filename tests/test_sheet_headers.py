from types import SimpleNamespace
import pytest
from services.sheets import SheetsService
from pipeline.energy import ENERGY_HEADERS


class Request:
    def __init__(self, result): self.result=result
    def execute(self): return self.result()


def service(table):
    def get_values(**kwargs):return Request(lambda: {'values':table[:1]})
    def update(**kwargs):
        def action():
            if table:table[0]=kwargs['body']['values'][0]
            else:table.extend(kwargs['body']['values'])
            return {}
        return Request(action)
    def append(**kwargs):
        assert kwargs['valueInputOption']=='RAW'
        return Request(lambda: table.extend(kwargs['body']['values']) or {})
    values=SimpleNamespace(get=get_values, update=update, append=append)
    def batch(**kwargs):return Request(lambda: table.insert(0,[]) or {})
    api=SimpleNamespace(values=lambda:values,batchUpdate=batch,
        get=lambda **kwargs:Request(lambda:{'sheets':[{'properties':{'title':'bd','sheetId':1}}]}))
    sh=object.__new__(SheetsService);sh.spreadsheet_id='id';sh.api=SimpleNamespace(spreadsheets=lambda:api)
    return sh


def test_empty_sheet_gets_headers_and_preserves_account_zero():
    table=[];sh=service(table)
    row=['id','', 'Sede','03268766','355379219-1',0,2025,6,'2025-05-04','2025-06-04','link','',100]
    sh.append('bd',row,headers=ENERGY_HEADERS)
    assert table==[ENERGY_HEADERS,row]


def test_legacy_invoice_is_preserved_when_adding_headers():
    original=['a'*30,'','Sede','03268766','355379219-1',0,2025,6,'','','https://drive.google.com/file/d/id/view','']
    table=[original.copy()];sh=service(table)
    sh.ensure_headers('bd',ENERGY_HEADERS)
    assert table==[ENERGY_HEADERS,original]


def test_unknown_sheet_layout_is_not_overwritten():
    table=[['Columna desconocida']];sh=service(table)
    with pytest.raises(RuntimeError,match='incompatibles'):sh.ensure_headers('bd',ENERGY_HEADERS)
    assert table==[['Columna desconocida']]
