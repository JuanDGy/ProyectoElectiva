"""Promoción champion/challenger: compara métricas antes de desplegar."""
from __future__ import annotations

import argparse

from ml.registry import get_champion, load_registry, promote


def main() -> int:
    parser = argparse.ArgumentParser(description="Promover un candidato a champion.")
    parser.add_argument("--candidate", default=None, help="ID del candidato (run).")
    parser.add_argument("--metric", choices=["mape", "mae", "rmse", "r2"], default="mape")
    parser.add_argument("--min-improvement", type=float, default=0.0)
    parser.add_argument("--list", action="store_true", help="Listar registro y salir.")
    args = parser.parse_args()

    registry = load_registry()
    if args.list or not args.candidate:
        champion = get_champion()
        print(f"Champion: {champion['id'] if champion else None}")
        for c in registry.get("challengers", []):
            print(f"  challenger {c['id']}: {c['metrics']}")
        return 0

    promoted, reason = promote(args.candidate, args.metric, args.min_improvement)
    print(reason)
    return 0 if promoted else 1


if __name__ == "__main__":
    raise SystemExit(main())
