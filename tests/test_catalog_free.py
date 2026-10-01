from pipeline.common import complete
from pipeline.consolidation import consolidate


def test_unknown_account_is_extracted_without_catalog():
    data={'cuenta':'0326876-6','numeroFactura':'355379219-1','kwh':0,
          'valorFacturado':125000,'inicioPeriodo':'2025-05-04','finPeriodo':'2025-06-04','año':2025,'mes':6}
    result=consolidate('id','link',data,data,office='CRISANTO LUQUE')
    assert result['Cuenta']=='03268766'
    assert result['NumeroFactura']=='355379219-1'
    assert result['ValorFacturado']==125000
    assert complete(result)


def test_conflicting_accounts_require_review():
    result=consolidate('id','link',{'cuenta':'0326876-6'},{'cuenta':'7223336-4'},office='CRISANTO LUQUE')
    assert result['Cuenta'] is None
    assert 'cuenta' in result['Observaciones'].lower()


def test_missing_office_requires_review():
    assert not complete({'Cuenta':'03268766','NumeroFactura':'355379219-1','kWh':0,
                         'ValorFacturado':100,'Año':2025,'Mes':6,
                         'InicioPeriodo':'2025-05-04','FinPeriodo':'2025-06-04'})
