"""Account-level consumption drift, calendar coverage and data freshness."""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from ml.dataset import DATASET_VERSION, prepare_history
from ml.registry import get_champion
from ml.snapshot import latest_snapshot

REPORTS_DIR = Path('reports')
DRIFT_THRESHOLD_PCT = 25.0
STALE_DAYS = 60


def check(history_csv, drift_threshold_pct=DRIFT_THRESHOLD_PCT):
    champion = get_champion()
    if champion is None:
        raise RuntimeError('Sin champion registrado; evalúa y promueve un candidato elegible.')
    if champion.get('dataset_version') != DATASET_VERSION:
        raise RuntimeError('Champion incompatible: reentrena con la preparación v1.')
    import joblib
    model_path = Path(champion.get('model_path', ''))
    spec = joblib.load(model_path).ecodata_spec_ if model_path.is_file() else {}
    raw = pd.read_csv(history_csv, dtype=str, keep_default_na=False)
    df, _, quality = prepare_history(raw.to_dict('records'), spec.get('include_flagged', False), spec.get('quality_rules'))
    if df.empty:
        raise RuntimeError('Sin histórico válido para monitoreo.')
    baseline = champion.get('baseline_per_account', {})
    drifted, missing, gaps = [], [], []
    for account, group in df.groupby('Cuenta'):
        recent_mean = float(group.sort_values('periodo').Consumo.iloc[-2:].mean())
        base = baseline.get(account)
        if base is None:
            missing.append(account)
        else:
            mean = base['mean_consumo']
            change = abs(recent_mean - mean) / abs(mean) * 100 if mean else (0.0 if recent_mean == 0 else None)
            if change is None or change >= drift_threshold_pct:
                drifted.append({'account': account, 'baseline_mean': mean, 'recent_mean': recent_mean,
                                'change_pct': change, 'zero_baseline_change': change is None})
        expected = pd.date_range(group.periodo.min(), group.periodo.max(), freq='MS')
        absent = expected.difference(group.periodo.unique())
        if len(absent):
            gaps.append({'account': account, 'missing_months': len(absent), 'periods': absent.strftime('%Y-%m').tolist()})
    now = datetime.now()
    age = (now - datetime.fromisoformat(champion['timestamp'])).days
    latest = df.periodo.max()
    lag_months = max(0, now.year * 12 + now.month - latest.year * 12 - latest.month)
    stale = lag_months > 2
    return {'timestamp': now.isoformat(timespec='seconds'), 'champion': champion['id'],
            'champion_age_days': age, 'champion_stale': age > STALE_DAYS,
            'drift_threshold_pct': drift_threshold_pct, 'drifted_accounts': drifted,
            'accounts_without_baseline': missing, 'coverage_gaps': gaps,
            'data_latest_period': latest.strftime('%Y-%m'), 'data_lag_months': lag_months, 'data_stale': stale,
            'quality': quality, 'needs_retrain': bool(drifted) or bool(missing) or age > STALE_DAYS or stale}


def main():
    parser = argparse.ArgumentParser(description='Monitorear consumo por cuenta y vigencia de datos.')
    parser.add_argument('--data')
    parser.add_argument('--drift-threshold', type=float, default=DRIFT_THRESHOLD_PCT)
    parser.add_argument('--reports-dir', default=str(REPORTS_DIR))
    args = parser.parse_args()
    snapshot = latest_snapshot() if not args.data else None
    source = args.data or (snapshot['file'] if snapshot else None)
    if not source:
        raise RuntimeError('Sin snapshot; indica --data.')
    report = check(source, args.drift_threshold)
    destination = Path(args.reports_dir); destination.mkdir(parents=True, exist_ok=True)
    path = destination / f'monitor_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'Reporte: {path}')
    return 2 if report['needs_retrain'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
