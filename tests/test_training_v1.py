import json
from pathlib import Path
import joblib
import pandas as pd
import pytest
import ml.train as training
from ml.predict import predict
from ml.registry import add_challenger, promote


def test_training_exports_aligned_sets_and_does_not_auto_promote(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = [{"Oficina": "A", "Cuenta": a, "N° Factura": f"{a}-{m}", "Año": 2025, "Mes": m,
             "kWh": base + m * 10} for a, base in [("001", 100), ("002", 200), ("003", 300)] for m in range(1, 13)]
    csv = tmp_path / "history.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    entry = training.train('linear', str(csv), test_start='2025-11-01', test_end='2025-12-01')
    directory = Path(entry['model_path']).parent
    train = pd.read_csv(directory / 'train.csv', dtype={'Cuenta': str})
    test = pd.read_csv(directory / 'test.csv', dtype={'Cuenta': str})
    assert train.periodo.max() == '2025-10-01'
    assert test.periodo.min() == '2025-11-01'
    assert len(test) == 6
    assert (test.target_consumo == test.Consumo).all()
    assert json.loads(Path('models/registry.json').read_text())['champion'] is None
    assert entry['data_sha256'] and entry['evaluation_id']
    assert entry['baseline_metrics']['persistence']['mae'] == 10
    model = joblib.load(entry['model_path'])
    assert model.ecodata_spec_['trained_accounts'] == ['001', '002', '003']
    value = predict(None, 2025, 11, entry['model_path'], str(csv), account='001')
    assert value == pytest.approx(210, abs=1)


def test_promotion_rejects_candidate_worse_than_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = tmp_path / 'model.joblib'; model.write_bytes(b'test')
    add_challenger({'id': 'bad', 'model_path': str(model), 'metrics': {'mae': 20},
                    'dataset_version': 'account-calendar-v1', 'evaluation_id': 'same',
                    'baseline_metrics': {'persistence': {'mae': 10}, 'rolling_mean': {'mae': 11}},
                    'promotion_eligible': False})
    promoted, reason = promote('bad', metric='mae')
    assert not promoted
    assert 'baseline' in reason.lower()


def test_promotion_refuses_scores_from_different_test_sets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    champion = {'id': 'old', 'metrics': {'mae': 10}, 'evaluation_id': 'november'}
    Path('models').mkdir()
    Path('models/registry.json').write_text(json.dumps({'champion': champion, 'challengers': []}))
    add_challenger({'id': 'new', 'metrics': {'mae': 1}, 'evaluation_id': 'december', 'promotion_eligible': True,
                    'baseline_metrics': {'persistence': {'mae': 2}, 'rolling_mean': {'mae': 3}}})
    promoted, reason = promote('new', metric='mae')
    assert not promoted
    assert 'evaluación' in reason.lower()


def test_gap_policy_coverage_counts_extra_predictions_separately():
    common = pd.DataFrame({'target_consumo': [10, 20], 'prediction': [8, 22]})
    extra = pd.DataFrame({'target_consumo': [30], 'prediction': [27]})
    report = training.coverage_report(common, extra, observed_n=4)
    assert report['common_test_coverage_pct'] == 50
    assert report['policy_prediction_coverage_pct'] == 75
    assert report['policy_abstentions'] == 1
    assert report['additional_gap_metrics']['mae'] == 3
    assert report['policy_metrics']['mae'] == pytest.approx(7/3)
