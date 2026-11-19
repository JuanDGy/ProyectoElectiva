"""Experimento v2: compara enfoques (nivel/delta/razon) y familias de modelos.

Selecciona hiperparametros SOLO con validacion expansiva previa al test y
reporta el test comun con significancia pareada frente a la persistencia.
No modifica modelos, registro ni hojas: solo escribe reports/.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import (ExtraTreesRegressor, GradientBoostingRegressor,
                              HistGradientBoostingRegressor, RandomForestRegressor)
from sklearn.linear_model import HuberRegressor, LinearRegression, Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.svm import SVR
from ml.neural_models import LSTMRegressor, RBFRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.dataset import (NUMERIC_FEATURES, SEASONAL_FEATURES, TARGET, build_features,
                        load_quality_rules, prepare_history, regression_metrics, temporal_split)
from ml.snapshot import latest_snapshot

TEST_START, TEST_END = '2025-11-01', '2025-12-01'
VALIDATION_MONTHS = 4


def _features(seasonal: bool) -> list[str]:
    return ['Cuenta', *NUMERIC_FEATURES, *(SEASONAL_FEATURES if seasonal else [])]


def _make(model: str, seasonal: bool, **params):
    numeric = NUMERIC_FEATURES + (SEASONAL_FEATURES if seasonal else [])
    pre = ColumnTransformer([
        ('num', StandardScaler(), numeric),
        ('account', OneHotEncoder(handle_unknown='ignore', drop='first', sparse_output=False), ['Cuenta']),
    ])
    if model == 'linear':
        est = LinearRegression()
    elif model == 'ridge':
        est = Ridge(alpha=params['alpha'])
    elif model == 'huber':
        est = HuberRegressor(alpha=params['alpha'], epsilon=1.35, max_iter=500)
    elif model == 'rf':
        est = RandomForestRegressor(n_estimators=300, max_depth=params['max_depth'],
                                    min_samples_leaf=params['min_samples_leaf'], random_state=42, n_jobs=1)
    elif model == 'rf_mae':
        est = RandomForestRegressor(n_estimators=300, criterion='absolute_error', max_depth=params['max_depth'],
                                    min_samples_leaf=params['min_samples_leaf'], random_state=42, n_jobs=1)
    elif model == 'et':
        est = ExtraTreesRegressor(n_estimators=300, max_depth=params['max_depth'],
                                  min_samples_leaf=3, random_state=42, n_jobs=1)
    elif model == 'gbr':
        est = GradientBoostingRegressor(n_estimators=200, learning_rate=0.05, max_depth=params['max_depth'],
                                        random_state=42)
    elif model == 'hgb':
        est = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=8,
                                            l2_regularization=params['l2'], random_state=42)
    elif model == 'rbf':
        est = RBFRegressor(centers=params['centers'], gamma=params['gamma'], alpha=1.0)
    elif model == 'mlp':
        est = MLPRegressor(hidden_layer_sizes=(params['hidden'],), alpha=10.0,
                           learning_rate_init=0.005, max_iter=350, random_state=42)
    elif model == 'svr':
        est = SVR(kernel='rbf', C=params['c'], epsilon=10.0)
    elif model == 'lstm':
        est = LSTMRegressor(hidden_size=params['hidden'], epochs=params['epochs'])
    elif model == 'mean':
        est = DummyRegressor(strategy='mean')
    else:
        raise ValueError(model)
    return Pipeline([('preprocess', pre), ('model', est)])


GRID = [
    ('mean', {}),
    ('linear', {}),
    ('ridge', {'alpha': 0.1}), ('ridge', {'alpha': 1.0}), ('ridge', {'alpha': 10.0}),
    ('huber', {'alpha': 1e-4}), ('huber', {'alpha': 1e-2}),
    ('rf', {'max_depth': 4, 'min_samples_leaf': 3}), ('rf', {'max_depth': 4, 'min_samples_leaf': 5}),
    ('rf', {'max_depth': 8, 'min_samples_leaf': 3}), ('rf', {'max_depth': 8, 'min_samples_leaf': 5}),
    ('rf_mae', {'max_depth': 4, 'min_samples_leaf': 3}), ('rf_mae', {'max_depth': 8, 'min_samples_leaf': 5}),
    ('et', {'max_depth': 4}), ('et', {'max_depth': 8}),
    ('gbr', {'max_depth': 2}), ('gbr', {'max_depth': 3}),
    ('hgb', {'l2': 0.0}), ('hgb', {'l2': 1.0}),
    # RBF: neuronas gaussianas; MLP: red densa; SVR: kernel radial; LSTM: rezagos causales.
    ('rbf', {'centers': 12, 'gamma': 0.1}), ('rbf', {'centers': 24, 'gamma': 0.1}),
    ('mlp', {'hidden': 16}), ('svr', {'c': 50.0}),
    ('lstm', {'hidden': 12, 'epochs': 50}),
]


def _fit_predict(mode, model, seasonal, params, train, base_col, score):
    pipeline = _make(model, seasonal, **params)
    if mode == 'level':
        y = train[TARGET]
        pipeline.fit(train[_features(seasonal)], y)
        return np.maximum(0.0, pipeline.predict(score[_features(seasonal)]))
    if mode == 'delta':
        y = train[TARGET] - train[base_col]
        pipeline.fit(train[_features(seasonal)], y)
        return np.maximum(0.0, score[base_col].to_numpy() + pipeline.predict(score[_features(seasonal)]))
    y = train[TARGET] / (train[base_col] + 1.0)
    pipeline.fit(train[_features(seasonal)], y)
    return np.maximum(0.0, (score[base_col].to_numpy() + 1.0) * pipeline.predict(score[_features(seasonal)]))


def _align(features, reference):
    keys = reference[['Cuenta', 'periodo']]
    return keys.merge(features, on=['Cuenta', 'periodo'], how='left', validate='one_to_one')


def _bootstrap(diff: np.ndarray, n: int = 4000, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    means = np.array([diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)])
    return {'diff_mae': float(diff.mean()), 'ci_low': float(np.percentile(means, 2.5)),
            'ci_high': float(np.percentile(means, 97.5)), 'p_better': float((means < 0).mean())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Competencia temporal: regresión, árboles, RBF, MLP, SVR y LSTM.')
    parser.add_argument('--models', default='all', help='Familias separadas por coma (all = todas). Ej.: rbf,lstm,mlp,svr')
    parser.add_argument('--approaches', default='level,delta,ratio', help='level,delta,ratio')
    parser.add_argument('--test-start', default=TEST_START)
    parser.add_argument('--test-end', default=TEST_END)
    args = parser.parse_args(argv)
    chosen = {p.strip() for p in args.models.split(',')}
    available = {name for name, _ in GRID}
    if chosen != {'all'} and (not chosen or chosen - available):
        parser.error('Modelos disponibles: ' + ', '.join(sorted(available)))
    approaches = [x.strip() for x in args.approaches.split(',')]
    if not approaches or any(x not in ('level', 'delta', 'ratio') for x in approaches):
        parser.error('Enfoques válidos: level,delta,ratio')
    grid = [(name, params) for name, params in GRID if chosen == {'all'} or name in chosen]
    source = Path(latest_snapshot()['file'])
    rows = pd.read_csv(source, dtype=str, keep_default_na=False).to_dict('records')
    rules = load_quality_rules()
    print(f'Fuente: {source} | filas={len(rows)}')

    variants = {gap: build_features(rows, gap, False, rules) for gap in ('strict', 'causal')}
    reference = variants['strict']
    train_ref, test_ref = temporal_split(reference, 2, args.test_start, args.test_end)
    # Misma poblacion que ml/train.py: cuentas vistas en entrenamiento.
    test_ref = test_ref[test_ref.Cuenta.isin(train_ref.Cuenta.unique())].reset_index(drop=True)
    test_min, test_max = test_ref.periodo.min(), test_ref.periodo.max()
    clean, _, _ = prepare_history(rows, False, rules)
    observed_test = len(clean[clean.periodo.between(test_min, test_max)])
    validation = pd.date_range(end=test_min - pd.DateOffset(months=1), periods=VALIDATION_MONTHS, freq='MS')

    baselines = {'persistence': regression_metrics(test_ref[TARGET], test_ref.lag_1),
                 'rolling_mean': regression_metrics(test_ref[TARGET], test_ref.rolling_3)}
    floor = min(b['mae'] for b in baselines.values())
    print(f'Test comun: {len(test_ref)} filas | persistencia MAE={baselines["persistence"]["mae"]:.2f} '
          f'| media movil MAE={baselines["rolling_mean"]["mae"]:.2f}')

    results = []
    for mode in approaches:
        for model, params in grid:
            for seasonal in ((False, True) if model in {'linear', 'ridge', 'rf', 'et'} else (False,)):
                errors, actuals = [], []
                for period in validation:
                    prior = reference[reference.periodo.lt(period)]
                    valid_ref = reference[reference.periodo.eq(period) & reference.Cuenta.isin(prior.Cuenta.unique())]
                    if valid_ref.empty:
                        continue
                    fit = variants['strict'][variants['strict'].periodo.lt(period)]
                    score = _align(variants['strict'], valid_ref)
                    pred = _fit_predict(mode, model, seasonal, params, fit, 'lag_1', score)
                    errors.extend(pred.tolist())
                    actuals.extend(valid_ref[TARGET].tolist())
                val = regression_metrics(actuals, errors)
                fit = variants['strict'][variants['strict'].periodo.lt(test_min)]
                pred = _fit_predict(mode, model, seasonal, params, fit, 'lag_1', test_ref)
                test = regression_metrics(test_ref[TARGET], pred)
                diff = np.abs(pred - test_ref[TARGET].to_numpy()) - np.abs(test_ref.lag_1.to_numpy() - test_ref[TARGET].to_numpy())
                per_account = pd.DataFrame({'Cuenta': test_ref.Cuenta, 'e': np.abs(pred - test_ref[TARGET].to_numpy())})
                results.append({'approach': mode, 'model': model, 'seasonal': seasonal, 'params': params,
                                'validation_mae': val['mae'], 'test': test,
                                'macro_account_mae': float(per_account.groupby('Cuenta').e.mean().mean()),
                                'vs_persistence': _bootstrap(diff),
                                'beats_floor': test['mae'] < floor,
                                'significant': test['mae'] < floor and _bootstrap(diff)['p_better'] >= 0.95})
                print(f'  {mode:<6} {model:<7} seasonal={int(seasonal)} params={params} '
                      f'| CV MAE={val["mae"]:8.2f} | test MAE={test["mae"]:8.2f} WAPE={test["wape"]:5.2f}')

    # Poblacion operativa (incluye huecos cubiertos por la politica causal) de los 3 mejores.
    top = sorted(results, key=lambda r: r['test']['mae'])[:3]
    causal = variants['causal']
    extra_ref = causal[causal.periodo.between(test_min, test_ref.periodo.max()) & causal.Cuenta.isin(train_ref.Cuenta.unique())]
    extra_ref = extra_ref.merge(test_ref[['Cuenta', 'periodo']].assign(in_main=True), on=['Cuenta', 'periodo'], how='left')
    extra_ref = extra_ref[extra_ref.in_main.isna()].copy()
    for entry in top:
        fit = causal[causal.periodo.lt(test_min)]
        p_main = _fit_predict(entry['approach'], entry['model'], entry['seasonal'], entry['params'],
                              fit, 'lag_1', _align(causal, test_ref))
        p_extra = _fit_predict(entry['approach'], entry['model'], entry['seasonal'], entry['params'],
                               fit, 'lag_1', extra_ref) if len(extra_ref) else np.array([])
        truth = np.concatenate([test_ref[TARGET].to_numpy(), extra_ref[TARGET].to_numpy()])
        pred = np.concatenate([p_main, p_extra])
        entry['operational'] = {**regression_metrics(truth, pred), 'n_extra': int(len(extra_ref)),
                                'coverage_pct': 100 * len(truth) / observed_test}

    output = {'generated_at': datetime.now().isoformat(timespec='seconds'), 'source': str(source),
              'test_rows': len(test_ref), 'validation_periods': [p.strftime('%Y-%m') for p in validation],
              'baselines': baselines, 'eligibility_floor_mae': floor,
              'results': sorted(results, key=lambda r: r['test']['mae'])}
    reports = Path('reports')
    reports.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    table = pd.DataFrame([{'enfoque': r['approach'], 'modelo': r['model'], 'estacional': r['seasonal'],
                           'CV_MAE': round(r['validation_mae'], 2), 'test_MAE': round(r['test']['mae'], 2),
                           'test_WAPE': round(r['test']['wape'] or 0, 2), 'macro_MAE': round(r['macro_account_mae'], 2),
                           'P_mejor_que_persist': round(r['vs_persistence']['p_better'], 2),
                           'gana_piso': r['beats_floor'], 'significativo': r['significant']}
                          for r in output['results']])
    table.to_csv(reports / f'experiment_models_{stamp}.csv', index=False, encoding='utf-8')
    print('\n=== Ranking por test MAE (piso = persistencia ' + f'{floor:.2f}) — DIAGNOSTICO, no protocolo de seleccion ===')
    print(table.head(15).to_string(index=False))

    by_cv = {}
    for entry in results:
        best = by_cv.get(entry['approach'])
        if best is None or entry['validation_mae'] < best['validation_mae']:
            by_cv[entry['approach']] = entry
    print('\n=== Configuracion elegida SOLO por CV, antes de ver el test (protocolo honesto) ===')
    for approach, entry in by_cv.items():
        t, vp = entry['test'], entry['vs_persistence']
        print(f"{approach:<6} {entry['model']:<7} seasonal={int(entry['seasonal'])} {entry['params']} | "
              f"CV MAE={entry['validation_mae']:.2f} -> test MAE={t['mae']:.2f} WAPE={t['wape']:.2f}% "
              f"P(mejor)={vp['p_better']:.2f} IC95=[{vp['ci_low']:+.0f}, {vp['ci_high']:+.0f}]")
    output['selected_by_validation_only'] = {k: {kk: v[kk] for kk in ('model', 'seasonal', 'params', 'validation_mae', 'test', 'vs_persistence')}
                                             for k, v in by_cv.items()}
    print('\n=== Poblacion operativa de los 3 mejores ===')
    for entry in top:
        op = entry['operational']
        print(f"{entry['approach']:<6} {entry['model']:<7} -> MAE={op['mae']:.2f} WAPE={op['wape']:.2f}% "
              f"n={op['n']} cobertura={op['coverage_pct']:.1f}%")
    (reports / f'experiment_models_{stamp}.json').write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f'\nReporte: reports/experiment_models_{stamp}.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
