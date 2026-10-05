import json
from pathlib import Path
from datetime import datetime
import pandas as pd
from ml.monitor import check


def test_monitor_measures_accounts_separately_and_reports_calendar_gap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('models').mkdir()
    champion = {'id': 'v1', 'dataset_version': 'account-calendar-v1', 'timestamp': datetime.now().isoformat(),
                'baseline_per_account': {'001': {'mean_consumo': 10}, '002': {'mean_consumo': 100}}}
    Path('models/registry.json').write_text(json.dumps({'champion': champion, 'challengers': []}))
    rows = [{'Oficina': 'A', 'Cuenta': a, 'Año': 2025, 'Mes': m, 'kWh': value}
            for a, value in [('001', 10), ('002', 100)] for m in [1, 3]]
    source = tmp_path / 'history.csv'; pd.DataFrame(rows).to_csv(source, index=False)
    result = check(str(source))
    assert not result['drifted_accounts']
    assert result['accounts_without_baseline'] == []
    assert sorted(x['account'] for x in result['coverage_gaps']) == ['001', '002']
    assert all(x['missing_months'] == 1 for x in result['coverage_gaps'])
    assert result['data_latest_period'] == '2025-03'
    assert result['data_stale']
