"""Adaptador de inferencia persistente para enfoques level/delta/ratio.

Este tipo vive en un módulo importable para que joblib funcione tanto desde
la CLI de entrenamiento como desde la CLI de predicción.
"""
from __future__ import annotations

import numpy as np


class ForecastEstimator:
    def __init__(self, pipeline, approach: str, feature_names: list[str], spec: dict):
        self.pipeline = pipeline
        self.approach = approach
        self.feature_names = list(feature_names)
        self.ecodata_spec_ = spec

    def predict(self, features):
        base = features["lag_1"].to_numpy(dtype=float)
        raw = np.asarray(self.pipeline.predict(features[self.feature_names]), dtype=float)
        if self.approach == "delta":
            raw = base + raw
        elif self.approach == "ratio":
            raw = (base + 1.0) * raw
        return np.maximum(raw, 0.0)
