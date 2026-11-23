"""Train v1 with expanding validation, shared holdout and local evidence."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import itertools
import json
import os
from pathlib import Path
import uuid
os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from ml.dataset import (DATASET_VERSION, FEATURES, NUMERIC_FEATURES, SEASONAL_FEATURES, TARGET,
                        build_features, calendar_features, load_quality_rules, prepare_history, regression_metrics, temporal_split)
from ml.registry import CANDIDATES_DIR, add_challenger
from ml.snapshot import latest_snapshot


def _build_pipeline(model_type, seasonal, depth=4, leaf=3):
    numeric = NUMERIC_FEATURES + (SEASONAL_FEATURES if seasonal else [])
    pre = ColumnTransformer([
        ('num', StandardScaler(), numeric),
        ('account', OneHotEncoder(handle_unknown='ignore', drop='first', sparse_output=False), ['Cuenta'])
    ])
    if model_type == 'linear':
        estimator = LinearRegression()
    elif model_type == 'random_forest':
        estimator = RandomForestRegressor(n_estimators=300, max_depth=depth, min_samples_leaf=leaf,
                                          random_state=42, n_jobs=1)
    else:
        raise ValueError('model_type debe ser linear o random_forest')
    return Pipeline([('preprocess', pre), ('model', estimator)])


def _read_source(data_csv):
    if data_csv:
        source = Path(data_csv)
    else:
        snap = latest_snapshot()
        if not snap:
            raise RuntimeError('Sin snapshot local. Ejecuta ml.snapshot antes de entrenar; ML no consulta Sheets automáticamente.')
        source = Path(snap['file'])
        if hashlib.md5(source.read_bytes()).hexdigest() != snap['md5']:
            raise RuntimeError('El snapshot no coincide con su hash registrado.')
    content = source.read_bytes()
    return pd.read_csv(source, dtype=str, keep_default_na=False).to_dict('records'), source, hashlib.sha256(content).hexdigest(), hashlib.md5(content).hexdigest()


def _align(features, reference):
    keys = reference[['Cuenta', 'periodo']]
    return keys.merge(features, on=['Cuenta', 'periodo'], how='left', validate='one_to_one')


def _score(model, data):
    # Negative physical consumption is clipped in both evaluation and inference.
    return np.maximum(0.0, model.predict(data[FEATURES]))


def _baseline_metrics(data):
    return {'persistence': regression_metrics(data[TARGET], data.lag_1),
            'rolling_mean': regression_metrics(data[TARGET], data.rolling_3)}


def coverage_report(common, additional, observed_n):
    """Common-population scores and separate operational gap-policy coverage."""
    combined = pd.concat([common, additional], ignore_index=True)
    return {'common_test_coverage_pct': 100 * len(common) / observed_n,
            'policy_prediction_coverage_pct': 100 * len(combined) / observed_n,
            'policy_test_predictions': len(combined), 'policy_abstentions': observed_n - len(combined),
            'additional_gap_metrics': regression_metrics(additional[TARGET], additional.prediction) if len(additional) else None,
            'policy_metrics': regression_metrics(combined[TARGET], combined.prediction)}


def train(model_type='random_forest', data_csv=None, experiment='ecodata-consumo-v1', n_test_periods=2,
          test_start=None, test_end=None, validation_months=4, quality_rules_path=None):
    if validation_months < 2:
        raise ValueError('Se necesitan al menos dos meses de validación temporal.')
    rows, source, sha256, md5 = _read_source(data_csv)
    rules = load_quality_rules(quality_rules_path)
    # Reference scoring population is fixed before searching: strict calendar, no questionable targets.
    reference = build_features(rows, 'strict', False, rules)
    train_ref, test_ref = temporal_split(reference, n_test_periods, test_start, test_end)
    test_min, test_max = test_ref.periodo.min(), test_ref.periodo.max()
    validation_periods = pd.date_range(end=test_min - pd.DateOffset(months=1), periods=validation_months, freq='MS')
    folds = []
    for period in validation_periods:
        prior = reference[reference.periodo.lt(period)]
        valid = reference[reference.periodo.eq(period) & reference.Cuenta.isin(prior.Cuenta.unique())]
        if len(prior) < 12 or valid.empty:
            raise RuntimeError(f'Validación insuficiente para {period.date()}; indica otro corte o menos meses.')
        folds.append((period, valid))
    test_ref = test_ref[test_ref.Cuenta.isin(train_ref.Cuenta.unique())].reset_index(drop=True)
    if test_ref.empty:
        raise RuntimeError('Ninguna cuenta del test tiene histórico de entrenamiento.')
    variants = {(gap, flagged): build_features(rows, gap, flagged, rules)
                for gap, flagged in itertools.product(['strict', 'causal'], [False, True])}
    forest_params = [(4, 3), (4, 5), (8, 3), (8, 5)] if model_type == 'random_forest' else [(None, None)]
    validation_results = []
    best = None
    for gap, flagged, seasonal, (depth, leaf) in itertools.product(['strict', 'causal'], [False, True], [False, True], forest_params):
        feature_rows = variants[(gap, flagged)]
        errors, actuals, fold_scores = [], [], []
        for period, valid_ref in folds:
            fit_rows = feature_rows[feature_rows.periodo.lt(period)]
            valid = _align(feature_rows, valid_ref)
            model = _build_pipeline(model_type, seasonal, depth, leaf)
            model.fit(fit_rows[FEATURES], fit_rows[TARGET])
            pred = _score(model, valid)
            errors.extend(pred.tolist()); actuals.extend(valid[TARGET].tolist())
            fold_scores.append({'target_month': period.strftime('%Y-%m'), 'train_rows': len(fit_rows),
                                'train_max_target': fit_rows.periodo.max().strftime('%Y-%m'),
                                'validation_rows': len(valid), 'metrics': regression_metrics(valid[TARGET], pred)})
        metrics = regression_metrics(actuals, errors)
        setting = {'gap_policy': gap, 'include_flagged': flagged, 'seasonal': seasonal,
                   'max_depth': depth, 'min_samples_leaf': leaf}
        result = {'parameters': setting, 'metrics': metrics, 'folds': fold_scores}
        validation_results.append(result)
        # Fixed deterministic tie order favors simpler/no-imputation/no-seasonality configuration.
        if best is None or metrics['mae'] < best['metrics']['mae'] - 1e-9:
            best = result
    setting = best['parameters']
    feature_rows = variants[(setting['gap_policy'], setting['include_flagged'])]
    train_df = feature_rows[feature_rows.periodo.lt(test_min)].reset_index(drop=True)
    test_df = _align(feature_rows, test_ref)
    pipeline = _build_pipeline(model_type, setting['seasonal'], setting['max_depth'], setting['min_samples_leaf'])
    pipeline.fit(train_df[FEATURES], train_df[TARGET])
    used_features = ['Cuenta', *NUMERIC_FEATURES, *(SEASONAL_FEATURES if setting['seasonal'] else [])]
    pipeline.ecodata_spec_ = {'dataset_version': DATASET_VERSION, **setting, 'features': used_features,
                             'quality_rules': rules, 'trained_accounts': sorted(train_df.Cuenta.unique().tolist()),
                             'train_target_end': train_df.periodo.max().strftime('%Y-%m'),
                             'prediction_protocol': 'rolling_one_month_prior_actuals', 'negative_predictions': 'clip_zero'}
    pred = _score(pipeline, test_df)
    metrics = regression_metrics(test_df[TARGET], pred)
    baselines = _baseline_metrics(test_ref)
    # Holdout truth/reference lags are independent of searched inclusion/imputation variants.
    evaluation_bytes = test_ref[['Cuenta', 'periodo', TARGET]].sort_values(['Cuenta', 'periodo']).to_csv(index=False).encode()
    evaluation_id = hashlib.sha256(evaluation_bytes).hexdigest()
    eligible = metrics['mae'] < min(b['mae'] for b in baselines.values())
    clean, excluded, quality = prepare_history(rows, setting['include_flagged'], rules)
    observed_test = clean[clean.periodo.between(test_min, test_max)]
    run_id = f'{model_type}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'
    candidate_dir = CANDIDATES_DIR / run_id
    candidate_dir.mkdir(parents=True, exist_ok=False)
    model_path = candidate_dir / 'model.joblib'
    joblib.dump(pipeline, model_path)
    train_df.to_csv(candidate_dir / 'train.csv', index=False)
    test_df.to_csv(candidate_dir / 'test.csv', index=False)
    clean.to_csv(candidate_dir / 'accepted_history.csv', index=False)
    excluded.to_csv(candidate_dir / 'excluded_rows.csv', index=False)
    quality.update(gap_policy=setting['gap_policy'], total_observed_test_targets=len(observed_test), scored_test_targets=len(test_df),
                   prediction_coverage_pct=100*len(test_df)/len(observed_test))
    (candidate_dir / 'quality_report.json').write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding='utf-8')
    (candidate_dir / 'validation.json').write_text(json.dumps(validation_results, indent=2, ensure_ascii=False), encoding='utf-8')
    predictions = test_df[['Cuenta', 'Sede', 'periodo', TARGET, 'lag_1_missing', 'history_age']].copy()
    predictions['prediction'] = pred
    predictions['persistence'] = test_ref.lag_1.to_numpy()
    predictions['rolling_mean'] = test_ref.rolling_3.to_numpy()
    predictions.to_csv(candidate_dir / 'test_predictions.csv', index=False)
    account_metrics = {a: regression_metrics(g[TARGET], g.prediction) for a, g in predictions.groupby('Cuenta')}
    # Additional rows are explicitly outside the common scoring population; no parameter selection uses them.
    extra_test = feature_rows[feature_rows.periodo.between(test_min, test_max) & feature_rows.Cuenta.isin(train_df.Cuenta.unique())]
    extra_test = extra_test.merge(test_ref[['Cuenta', 'periodo']].assign(in_main=True), on=['Cuenta', 'periodo'], how='left')
    extra_test = extra_test[extra_test.in_main.isna()].copy()
    if not extra_test.empty:
        extra_test['prediction'] = _score(pipeline, extra_test)
    extra_test.to_csv(candidate_dir / 'additional_gap_predictions.csv', index=False)
    sparse = feature_rows[feature_rows.periodo.gt(test_max) & feature_rows.Cuenta.isin(train_df.Cuenta.unique())].copy()
    if not sparse.empty:
        sparse['prediction'] = _score(pipeline, sparse)
    sparse.to_csv(candidate_dir / 'sparse_tail_predictions.csv', index=False)
    policy_coverage = coverage_report(predictions, extra_test, len(observed_test))
    quality.pop('prediction_coverage_pct', None)
    quality.update(policy_coverage)
    (candidate_dir / 'quality_report.json').write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding='utf-8')
    operational = pd.concat([predictions[['Cuenta', TARGET, 'prediction']], extra_test[['Cuenta', TARGET, 'prediction']]] if not extra_test.empty else [predictions[['Cuenta', TARGET, 'prediction']]], ignore_index=True)
    policy_account_metrics = {a: regression_metrics(g[TARGET], g.prediction) for a, g in operational.groupby('Cuenta')}
    code_root = Path(__file__).resolve().parent
    code_sha256 = hashlib.sha256(b''.join((code_root / name).read_bytes() for name in ['dataset.py', 'train.py', 'predict.py'])).hexdigest()
    entry = {'id': run_id, 'model_type': model_type, 'model_path': model_path.as_posix(),
             'dataset_version': DATASET_VERSION, 'features': used_features, 'parameters': setting,
             'metrics': metrics, 'validation_metrics': best['metrics'], 'baseline_metrics': baselines,
             'baseline_comparison_metric': 'mae', 'promotion_eligible': eligible,
             'evaluation_id': evaluation_id, 'train_rows': len(train_df), 'test_rows': len(test_df),
             'test_periods': sorted(test_df.periodo.dt.strftime('%Y-%m').unique().tolist()),
             'train_target_end': train_df.periodo.max().strftime('%Y-%m'),
             'validation_periods': [p.strftime('%Y-%m') for p in validation_periods],
             'data_snapshot': str(source), 'data_md5': md5, 'data_sha256': sha256,
             'quality_rules_sha256': hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest(),
             'quality': quality, 'per_account_metrics': account_metrics,
             'policy_per_account_metrics': policy_account_metrics, 'code_sha256': code_sha256,
             'macro_account_mae': float(np.mean([m['mae'] for m in account_metrics.values()])),
             'baseline_per_account': {a: {'mean_consumo': float(g.Consumo.mean()), 'n': len(g)} for a, g in clean[clean.periodo.lt(test_min)].groupby('Cuenta')},
             'prediction_protocol': 'rolling_one_month_prior_actuals',
             'timestamp': datetime.now().isoformat(timespec='seconds')}
    (candidate_dir / 'metadata.json').write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding='utf-8')
    # Tracking is local and records exact evidence, never invokes authentication or network services.
    import mlflow
    mlflow.set_tracking_uri('sqlite:///' + str(Path('mlruns/v1_tracking.db').resolve()))
    Path('mlruns').mkdir(exist_ok=True)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_id) as run:
        mlflow.log_params({'model_type': model_type, **setting, 'dataset_version': DATASET_VERSION,
                           'train_rows': len(train_df), 'test_rows': len(test_df)})
        mlflow.log_metrics({k: v for k, v in metrics.items() if v is not None})
        mlflow.set_tags({'data_sha256': sha256, 'evaluation_id': evaluation_id})
        for name in ['metadata.json', 'validation.json', 'quality_report.json', 'test_predictions.csv', 'model.joblib']:
            mlflow.log_artifact(str(candidate_dir / name))
        entry['mlflow_run_id'] = run.info.run_id
    (candidate_dir / 'metadata.json').write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding='utf-8')
    add_challenger(entry)
    print(f'{model_type}: CV MAE={best["metrics"]["mae"]:.3f}; test={metrics}; elegible={eligible}; modelo={model_path}')
    return entry


def train_models(data_csv=None, **kwargs):
    entries = [train(model, data_csv, **kwargs) for model in ['linear', 'random_forest']]
    if len({e['evaluation_id'] for e in entries}) != 1:
        raise RuntimeError('Los candidatos no comparten el mismo conjunto de evaluación.')
    selected = min(entries, key=lambda e: e['validation_metrics']['mae'])
    comparison = {'candidates': [{k: e[k] for k in ['id', 'model_type', 'model_path', 'parameters', 'validation_metrics', 'metrics', 'baseline_metrics', 'promotion_eligible', 'train_rows', 'test_rows']} for e in entries],
                  'selected_by_pre_test_validation': selected['id'], 'evaluation_id': selected['evaluation_id'],
                  'source_sha256': selected['data_sha256'], 'auto_promoted': False}
    Path('reports').mkdir(exist_ok=True)
    output = Path('reports') / f'comparison_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    output.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Comparación: {output}; seleccionado por CV: {selected["id"]}')
    return comparison


def main():
    parser = argparse.ArgumentParser(description='Entrenamiento reproducible v1 por cuenta/mes.')
    parser.add_argument('--model', choices=['linear', 'random_forest', 'both'], default='both')
    parser.add_argument('--data')
    parser.add_argument('--experiment', default='ecodata-consumo-v1')
    parser.add_argument('--n-test-periods', type=int, default=2)
    parser.add_argument('--test-start')
    parser.add_argument('--test-end')
    parser.add_argument('--validation-months', type=int, default=4)
    parser.add_argument('--quality-rules')
    args = parser.parse_args()
    options = dict(experiment=args.experiment, n_test_periods=args.n_test_periods, test_start=args.test_start,
                   test_end=args.test_end, validation_months=args.validation_months, quality_rules_path=args.quality_rules)
    if args.model == 'both':
        train_models(args.data, **options)
    else:
        train(args.model, args.data, **options)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
