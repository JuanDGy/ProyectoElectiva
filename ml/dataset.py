"""Account/month dataset and causal features shared by fitting and inference."""
from __future__ import annotations
import json
import math
from pathlib import Path
import unicodedata
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

DATASET_VERSION = 'account-calendar-v1'
NUMERIC_FEATURES = ['lag_1', 'lag_2', 'rolling_3', 'history_count_3', 'lag_1_missing', 'lag_2_missing', 'history_age']
SEASONAL_FEATURES = ['month_sin', 'month_cos']
FEATURES = ['Cuenta', *NUMERIC_FEATURES, *SEASONAL_FEATURES]
TARGET = 'target_consumo'
REQUIRED = {'Sede', 'Año', 'Mes', 'Consumo'}
RULES_PATH = Path(__file__).with_name('quality_rules.json')


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    names = {'oficina': 'Sede', 'sede': 'Sede', 'ano': 'Año', 'mes': 'Mes', 'kwh': 'Consumo', 'consumo': 'Consumo',
             'cuenta': 'Cuenta', 'nfactura': 'Factura', 'numerofactura': 'Factura', 'factura': 'Factura',
             'inicioperiodo': 'InicioPeriodo', 'finperiodo': 'FinPeriodo', 'valorfacturado': 'ValorFacturado', 'valortotal': 'ValorFacturado'}
    rename = {}
    for col in df.columns:
        normalized = unicodedata.normalize('NFKD', str(col).lower())
        key = ''.join(c for c in normalized if c.isalnum() and not unicodedata.combining(c))
        if key in names:
            rename[col] = names[key]
    result = df.rename(columns=rename)
    if result.columns.duplicated().any():
        raise RuntimeError('Columnas ambiguas tras normalización.')
    return result


def validate_dataset(df: pd.DataFrame) -> list[str]:
    missing = REQUIRED - set(df.columns)
    if missing:
        raise RuntimeError('Faltan columnas para entrenamiento: ' + ', '.join(sorted(missing)))
    if df.empty:
        raise RuntimeError('Dataset vacío: sin filas para entrenamiento.')
    warnings = []
    for col in sorted(REQUIRED):
        count = int(df[col].replace('', pd.NA).isna().sum())
        if count:
            warnings.append(f'Columna {col}: {count} valores nulos.')
    key = ['Cuenta', 'Año', 'Mes'] if 'Cuenta' in df else ['Sede', 'Año', 'Mes']
    count = int(df.duplicated(key).sum())
    if count:
        warnings.append(f'{count} filas duplicadas por {key}: revisar identidad y conflictos.')
    if 'Cuenta' not in df:
        warnings.append('Sin Cuenta: compatibilidad con una serie por sede; no usar si hay varias cuentas.')
    return warnings


def load_quality_rules(path: str | Path | None = None) -> dict:
    source = Path(path) if path else RULES_PATH
    return json.loads(source.read_text(encoding='utf-8')) if source.exists() else {}


def prepare_history(rows: list[dict], include_flagged: bool = False, quality_rules: dict | None = None):
    df = normalize_columns(pd.DataFrame(rows)).copy()
    warnings = validate_dataset(df)
    df['source_row'] = range(2, len(df) + 2)
    if 'Cuenta' not in df:
        df['Cuenta'] = 'sede:' + df.Sede.astype(str)
    else:
        df['Cuenta'] = df.Cuenta.fillna('').astype(str).str.strip()
    df['Sede'] = df.Sede.fillna('').astype(str).str.strip()
    for col in ['Año', 'Mes', 'Consumo']:
        text = df[col].astype(str).str.strip()
        comma = text.str.contains(',', regex=False)
        text.loc[comma] = text.loc[comma].str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
        df[col] = pd.to_numeric(text, errors='coerce')
    valid = df.Año.notna() & df.Mes.notna() & df.Consumo.notna()
    valid &= df.Año.between(1900, 2200) & df.Mes.between(1, 12) & df.Consumo.ge(0)
    valid &= df.Año.mod(1).eq(0) & df.Mes.mod(1).eq(0)
    valid &= np.isfinite(df.Consumo)
    df['periodo'] = pd.to_datetime(dict(year=df.Año.where(valid), month=df.Mes.where(valid), day=1), errors='coerce')
    reason = pd.Series('', index=df.index)
    reason.loc[~valid | df.periodo.isna() | df.Cuenta.eq('') | df.Sede.eq('')] = 'invalid_required_fields'
    for col in ['InicioPeriodo', 'FinPeriodo']:
        if col in df:
            reason.loc[reason.eq('') & pd.to_datetime(df[col], errors='coerce').isna()] = 'invalid_billing_dates'
    if {'InicioPeriodo', 'FinPeriodo'} <= set(df):
        start = pd.to_datetime(df.InicioPeriodo, errors='coerce')
        end = pd.to_datetime(df.FinPeriodo, errors='coerce')
        bad = start.gt(end) | end.dt.year.ne(df.Año) | end.dt.month.ne(df.Mes)
        reason.loc[reason.eq('') & bad] = 'inconsistent_billing_period'
    rules = quality_rules if quality_rules is not None else load_quality_rules()
    flagged = {(str(v['account']), str(v['invoice'])) for v in rules.get('questionable_consumption', [])}
    df['quality_flagged'] = False
    if 'Factura' in df:
        df['Factura'] = df.Factura.fillna('').astype(str).str.strip()
        df['quality_flagged'] = [(a, b) in flagged for a, b in zip(df.Cuenta, df.Factura)]
        reason.loc[reason.eq('') & df.Factura.eq('')] = 'missing_invoice_identity'
        active = df[reason.eq('')]
        # A later calendar record cannot invalidate a previously usable invoice.
        # Preserve its earliest period, quarantine any later conflicting period.
        earliest = active.groupby(['Cuenta', 'Factura']).periodo.transform('min')
        later_conflict = active.periodo.ne(earliest)
        reason.loc[later_conflict[later_conflict].index] = 'conflicting_invoice_period'
        active = df[reason.eq('')]
        fields = ['Consumo', 'Año', 'Mes', *[c for c in ['InicioPeriodo', 'FinPeriodo'] if c in df]]
        conflicts = active.groupby(['Cuenta', 'Factura'])[fields].transform('nunique').gt(1).any(axis=1)
        reason.loc[conflicts[conflicts].index] = 'conflicting_invoice_measurements'
        active = df[reason.eq('')]
        repeated = active.duplicated(['Cuenta', 'Factura'], keep='first')
        reason.loc[repeated[repeated].index] = 'repeated_invoice'
        active = df[reason.eq('')]
        repeated = active.duplicated(['Cuenta', 'periodo'], keep=False)
        reason.loc[repeated[repeated].index] = 'different_invoices_same_period'
    else:
        active = df[reason.eq('')]
        conflicts = active.groupby(['Cuenta', 'periodo']).Consumo.transform('nunique').gt(1)
        reason.loc[conflicts[conflicts].index] = 'conflicting_period_measurements'
        active = df[reason.eq('')]
        repeated = active.duplicated(['Cuenta', 'periodo'], keep='first')
        reason.loc[repeated[repeated].index] = 'repeated_measurement'
    if not include_flagged:
        reason.loc[reason.eq('') & df.quality_flagged] = 'questionable_consumption'
    df['quality_reason'] = reason
    excluded = df[reason.ne('')].copy()
    clean = df[reason.eq('')].sort_values(['Cuenta', 'periodo']).reset_index(drop=True)
    report = {'dataset_version': DATASET_VERSION, 'source_rows': len(df), 'accepted_rows': len(clean),
              'excluded_rows': len(excluded), 'exclusion_reasons': excluded.quality_reason.value_counts().to_dict(),
              'ambiguous_account_offices': int((df.groupby('Cuenta').Sede.nunique() > 1).sum()),
              'include_flagged': include_flagged, 'warnings': warnings}
    return clean, excluded, report


def calendar_features(history: pd.DataFrame, gap_policy: str = 'strict', target_period=None):
    if gap_policy not in {'strict', 'causal'}:
        raise ValueError('gap_policy debe ser strict o causal')
    frames = []
    for account, group in history.groupby('Cuenta', sort=True):
        group = group.sort_values('periodo').set_index('periodo')
        end = target_period if target_period is not None else group.index.max()
        grid = pd.date_range(group.index.min(), end, freq='MS')
        if not len(grid):
            continue
        y = group.Consumo.reindex(grid)
        previous, lag2 = y.shift(1), y.shift(2)
        previous_dates = pd.Series(grid, index=grid).where(y.notna()).shift(1).ffill()
        age = pd.Series(grid.year * 12 + grid.month, index=grid) - (previous_dates.dt.year * 12 + previous_dates.dt.month)
        last_observed = previous.ffill()
        eligible = previous.notna() if gap_policy == 'strict' else (last_observed.notna() & age.le(3))
        lag1 = previous if gap_policy == 'strict' else previous.fillna(last_observed)
        f = pd.DataFrame({'periodo': grid, 'Cuenta': str(account), 'Año': grid.year, 'Mes': grid.month,
                          'Consumo': y.values, TARGET: y.values, 'lag_1': lag1.values,
                          'lag_2': lag2.fillna(lag1).values,
                          'rolling_3': previous.rolling(3, min_periods=1).mean().values,
                          'history_count_3': previous.rolling(3, min_periods=1).count().values,
                          'lag_1_missing': previous.isna().astype(int).values,
                          'lag_2_missing': lag2.isna().astype(int).values, 'history_age': age.values,
                          'month_sin': np.sin(2 * np.pi * (grid.month - 1) / 12),
                          'month_cos': np.cos(2 * np.pi * (grid.month - 1) / 12), 'eligible': eligible.values})
        f['Sede'] = group.Sede.reindex(grid).fillna('').values
        f['quality_flagged'] = group.quality_flagged.reindex(grid).fillna(False).astype(bool).values
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=[*FEATURES, 'periodo', 'Consumo', 'Sede', TARGET, 'eligible', 'quality_flagged'])
    return pd.concat(frames, ignore_index=True)


def build_features(rows: list[dict], gap_policy='strict', include_flagged=False, quality_rules=None):
    history, _, report = prepare_history(rows, include_flagged, quality_rules)
    f = calendar_features(history, gap_policy)
    f = f[f.eligible & f[TARGET].notna()].reset_index(drop=True)
    f.attrs['quality_report'] = report
    return f


def prediction_features(rows, account, year, month, gap_policy='strict', include_flagged=False, quality_rules=None):
    target = pd.Timestamp(year=year, month=month, day=1)
    raw = normalize_columns(pd.DataFrame(rows))
    years, months = pd.to_numeric(raw['Año'], errors='coerce'), pd.to_numeric(raw['Mes'], errors='coerce')
    # Filter before deduplication: future conflicts cannot alter historical predictions.
    past = (years * 12 + months) < (year * 12 + month)
    if not past.any():
        raise RuntimeError('Sin histórico anterior al mes solicitado.')
    history, _, _ = prepare_history(raw.loc[past].to_dict('records'), include_flagged, quality_rules)
    selected = history[history.Cuenta.eq(str(account))]
    if selected.empty:
        raise RuntimeError(f'Sin histórico anterior al mes solicitado para la cuenta {account}.')
    f = calendar_features(selected, gap_policy, target)
    f = f[f.periodo.eq(target)]
    if f.empty or not bool(f.eligible.iloc[0]):
        raise RuntimeError('Sin consumo del mes anterior o histórico demasiado antiguo; predicción abstendida.')
    return f.reset_index(drop=True)


def temporal_split(df, n_test_periods=2, test_start=None, test_end=None):
    if n_test_periods < 1:
        raise ValueError('n_test_periods debe ser positivo')
    counts = df.groupby('periodo').size()
    if counts.empty:
        raise RuntimeError('Sin filas elegibles para split temporal.')
    end = pd.Timestamp(test_end) if test_end else counts[counts.ge(counts.max() * .5)].index.max()
    start = pd.Timestamp(test_start) if test_start else end - pd.DateOffset(months=n_test_periods - 1)
    if start > end:
        raise ValueError('El inicio de test debe ser anterior al fin')
    train_df = df[df.periodo.lt(start)].reset_index(drop=True)
    test_df = df[df.periodo.between(start, end)].reset_index(drop=True)
    if len(train_df) < 12:
        raise RuntimeError(f'Entrenamiento insuficiente tras el split: {len(train_df)} filas.')
    if test_df.empty:
        raise RuntimeError('El split temporal dejó el conjunto de prueba vacío.')
    return train_df, test_df


def regression_metrics(y_true, y_pred):
    y, p = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    if not len(y) or not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ValueError('Métricas requieren valores finitos y observaciones.')
    r2 = float(r2_score(y, p)) if len(y) >= 2 else None
    if r2 is not None and not math.isfinite(r2):
        r2 = None
    mask = y > 0
    denominator = float(np.abs(y).sum())
    return {'mae': float(mean_absolute_error(y, p)), 'rmse': float(math.sqrt(mean_squared_error(y, p))),
            'r2': r2, 'mape': float((abs((y[mask] - p[mask]) / y[mask])).mean() * 100) if mask.any() else None,
            'mape_n': int(mask.sum()), 'n': int(len(y)),
            'wape': float(np.abs(y - p).sum() / denominator * 100) if denominator else None}
