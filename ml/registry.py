"""Registro local de modelos: champion vs challengers (JSON versionado)."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

REGISTRY_PATH = Path("models/registry.json")
CANDIDATES_DIR = Path("models/candidates")
CHAMPION_DIR = Path("models/champion")


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    if not path.exists():
        return {"champion": None, "challengers": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(registry: dict, path: Path = REGISTRY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def add_challenger(entry: dict, path: Path = REGISTRY_PATH) -> dict:
    registry = load_registry(path)
    registry["challengers"] = [
        c for c in registry.get("challengers", []) if c["id"] != entry["id"]
    ]
    registry["challengers"].append(entry)
    save_registry(registry, path)
    return registry


def get_champion(path: Path = REGISTRY_PATH) -> dict | None:
    return load_registry(path).get("champion")


def champion_model_path(path: Path = REGISTRY_PATH) -> Path | None:
    champion = get_champion(path)
    if not champion:
        return None
    model_path = CHAMPION_DIR / "model.joblib"
    return model_path if model_path.exists() else None


def promote(
    candidate_id: str,
    metric: str = "mae",
    min_improvement: float = 0.0,
    path: Path = REGISTRY_PATH,
) -> tuple[bool, str]:
    """Promueve un challenger a champion si mejora la métrica (menor es mejor).

    Devuelve (promovido, motivo). Métricas soportadas: mape, mae, rmse.
    r2 es 'mayor es mejor' y también se soporta.
    """
    registry = load_registry(path)
    candidate = next(
        (c for c in registry.get("challengers", []) if c["id"] == candidate_id), None
    )
    if candidate is None:
        return False, f"Candidato {candidate_id} no existe en el registro."
    if candidate["metrics"].get(metric) is None:
        return False, f"El candidato no tiene métrica {metric}."
    if not math.isfinite(candidate['metrics'][metric]):
        return False, 'Métrica no finita.'
    baselines = candidate.get('baseline_metrics', {})
    candidate_mae = candidate['metrics'].get('mae')
    baseline_maes = [b.get('mae') for b in baselines.values()]
    if (not candidate.get('promotion_eligible') or not candidate.get('evaluation_id') or
            candidate_mae is None or not math.isfinite(candidate_mae) or not baseline_maes or
            any(v is None or not math.isfinite(v) for v in baseline_maes) or
            candidate_mae >= min(baseline_maes)):
        return False, 'No elegible: requiere evaluación v1 y mejora de MAE frente a cada baseline.'

    champion = registry.get("champion")
    if champion and champion.get('evaluation_id') != candidate['evaluation_id']:
        return False, 'Conjuntos de evaluación distintos: reevalúa ambos modelos en el mismo test.'
    higher_is_better = metric == "r2"
    if champion is None:
        _deploy(candidate, registry, path, motivo="sin champion previo")
        return True, "Promovido: no había champion."

    old = champion["metrics"].get(metric)
    new = candidate["metrics"][metric]
    if old is None:
        return False, f"El champion no tiene métrica {metric}; promoción manual requerida."
    improvement = (new - old) if higher_is_better else (old - new)
    if improvement <= 0 or improvement < min_improvement:
        return False, (
            f"Sin mejora suficiente en {metric}: champion={old:.4f}, "
            f"candidato={new:.4f} (mínimo exigido: {min_improvement})."
        )
    registry["challengers"] = [c for c in registry["challengers"] if c["id"] != champion["id"]]
    registry["challengers"].append(champion)
    _deploy(candidate, registry, path,
            motivo=f"{metric}: {old:.4f} -> {new:.4f}")
    return True, f"Promovido por {metric}: {old:.4f} -> {new:.4f}."


def _deploy(candidate: dict, registry: dict, path: Path, motivo: str) -> None:
    src = Path(candidate["model_path"])
    CHAMPION_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, CHAMPION_DIR / "model.joblib")
    (CHAMPION_DIR / "metadata.json").write_text(
        json.dumps(candidate, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    registry["champion"] = {**candidate, "promoted_for": motivo}
    registry["challengers"] = [
        c for c in registry.get("challengers", []) if c["id"] != candidate["id"]
    ]
    save_registry(registry, path)
