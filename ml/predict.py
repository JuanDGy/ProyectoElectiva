"""Calendar-aligned prediction, account identity and no future history."""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
import joblib
import pandas as pd
from ml.dataset import DATASET_VERSION, NUMERIC_FEATURES, normalize_columns, prediction_features
from ml.registry import champion_model_path
from ml.snapshot import latest_snapshot


def _account(history, sede=None, account=None, target=None):
    raw = normalize_columns(history)
    if target is not None:
        dates = pd.to_numeric(raw['Año'], errors='coerce') * 12 + pd.to_numeric(raw['Mes'], errors='coerce')
        raw = raw[dates.lt(target.year * 12 + target.month)]
    if account is not None:
        return str(account)
    if not sede:
        raise RuntimeError('Indica --cuenta o una sede con cuenta única.')
    if 'Cuenta' not in raw:
        return 'sede:' + sede
    accounts = raw.loc[raw.Sede.eq(sede), 'Cuenta'].astype(str).unique()
    if len(accounts) != 1:
        raise RuntimeError(f'La sede {sede} tiene {len(accounts)} cuentas; indica --cuenta. No se publican totales parciales como totales de sede.')
    return accounts[0]


def compute_lags(history: pd.DataFrame, sede: str | None = None, year=None, month=None,
                 account=None, gap_policy='strict', include_flagged=False):
    if year is None or month is None:
        raise RuntimeError('Indica año y mes objetivo para evitar usar historia futura.')
    target = pd.Timestamp(year=year, month=month, day=1)
    resolved = _account(history, sede, account, target)
    f = prediction_features(history.to_dict('records'), resolved, year, month, gap_policy, include_flagged)
    return {col: float(f.iloc[0][col]) for col in NUMERIC_FEATURES}


def predict(sede: str | None, year: int, month: int, model_path=None, data_csv=None,
            log=False, lags=None, account=None):
    if lags is not None:
        raise RuntimeError('La v1 requiere histórico verificable; no admite rezagos manuales.')
    resolved = Path(model_path) if model_path else champion_model_path()
    if resolved is None or not resolved.exists():
        raise RuntimeError('Sin modelo validado. Indica --model-path para probar un candidato o promueve uno elegible.')
    model = joblib.load(resolved)
    spec = getattr(model, 'ecodata_spec_', None)
    if not spec or spec.get('dataset_version') != DATASET_VERSION:
        raise RuntimeError('Modelo incompatible con la preparación temporal v1; reentrena.')
    snap = latest_snapshot() if not data_csv else None
    source = data_csv or (snap['file'] if snap else None)
    if not source:
        raise RuntimeError('Sin snapshot; indica --data o genera uno con ml.snapshot.')
    history = pd.read_csv(source, dtype=str, keep_default_na=False)
    target = pd.Timestamp(year=year, month=month, day=1)
    resolved_account = _account(history, sede, account, target)
    if resolved_account not in spec['trained_accounts']:
        raise RuntimeError(f'Cuenta {resolved_account} sin entrenamiento; requiere datos y reentrenamiento.')
    x = prediction_features(history.to_dict('records'), resolved_account, year, month,
                            spec['gap_policy'], spec['include_flagged'], spec['quality_rules'])
    value = max(0.0, float(model.predict(x[spec['features']])[0]))
    if log:
        # Existing Predicciones is a site-total schema; an account forecast cannot be appended there.
        raise RuntimeError('Predicción por cuenta: escritura en Predicciones requiere esquema y mapa de sede conciliados. Usa salida local.')
    print(f'Predicción cuenta [{resolved_account} {year}-{month:02d}]: {value:.3f} kWh ({resolved})')
    return value


def main():
    parser = argparse.ArgumentParser(description='Pronóstico local por cuenta, a un mes.')
    parser.add_argument('--sede')
    parser.add_argument('--cuenta')
    parser.add_argument('--year', required=True, type=int)
    parser.add_argument('--month', required=True, type=int)
    parser.add_argument('--model-path')
    parser.add_argument('--data')
    parser.add_argument('--no-log', action='store_true', help='Compatibilidad: la v1 siempre entrega salida local.')
    args = parser.parse_args()
    predict(args.sede, args.year, args.month, args.model_path, args.data, account=args.cuenta)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
