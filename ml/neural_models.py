"""Modelos experimentales: red de base radial y LSTM temporal sobre rezagos.

Entrenan únicamente con los datos entregados por cada fold. La LSTM observa
(lag_2, lag_1), dos meses en orden causal; el resto son covariables estáticas.
La historia actual es corta: no se presenta como una LSTM de series largas.
"""
from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.cluster import KMeans
from sklearn.linear_model import Ridge


class RBFRegressor(RegressorMixin, BaseEstimator):
    """Red RBF de una capa: centros K-Means, activaciones gaussianas y salida Ridge."""

    def __init__(self, centers: int = 16, gamma: float = 0.1, alpha: float = 1.0,
                 random_state: int = 42):
        self.centers = centers
        self.gamma = gamma
        self.alpha = alpha
        self.random_state = random_state

    def _basis(self, X):
        matrix = np.asarray(X, dtype=np.float64)
        distances = ((matrix[:, None, :] - self.centers_ [None, :, :]) ** 2).sum(axis=2)
        return np.exp(-self.gamma * distances)

    def fit(self, X, y):
        matrix = np.asarray(X, dtype=np.float64)
        target = np.asarray(y, dtype=np.float64)
        if matrix.ndim != 2 or not len(matrix) or not np.isfinite(matrix).all():
            raise ValueError("RBF requiere una matriz finita con al menos una fila")
        clusters = min(self.centers, len(matrix))
        self.centers_ = KMeans(n_clusters=clusters, random_state=self.random_state,
                               n_init=5).fit(matrix).cluster_centers_
        self.regressor_ = Ridge(alpha=self.alpha).fit(self._basis(matrix), target)
        self.n_features_in_ = matrix.shape[1]
        return self

    def predict(self, X):
        return self.regressor_.predict(self._basis(X))


class LSTMRegressor(RegressorMixin, BaseEstimator):
    """LSTM CPU, con secuencia de dos rezagos y cabeza densa para otras features.

    X debe seguir el orden del preprocesador: lag_1, lag_2, resto.
    Se normaliza y con estadísticas de entrenamiento; no hay holdout interno.
    """

    def __init__(self, hidden_size: int = 12, epochs: int = 50, learning_rate: float = 0.01,
                 random_state: int = 42):
        self.hidden_size = hidden_size
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.random_state = random_state

    @staticmethod
    def _inputs(X):
        matrix = np.asarray(X, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] < 2 or not np.isfinite(matrix).all():
            raise ValueError("LSTM requiere lag_1, lag_2 y covariables finitas")
        # La secuencia es cronológica: primero t-2, después t-1.
        return np.stack([matrix[:, 1], matrix[:, 0]], axis=1)[:, :, None], matrix[:, 2:]

    def fit(self, X, y):
        import torch
        from torch import nn

        sequence, context = self._inputs(X)
        target = np.asarray(y, dtype=np.float32)
        if not len(target) or not np.isfinite(target).all():
            raise ValueError("LSTM requiere objetivos finitos")
        torch.manual_seed(self.random_state)
        self.y_center_ = float(np.mean(target))
        self.y_scale_ = float(max(np.std(target), 1.0))
        self.n_features_in_ = 2 + context.shape[1]
        self.net_ = _SequenceNet(context.shape[1], self.hidden_size)
        optimizer = torch.optim.Adam(self.net_.parameters(), lr=self.learning_rate,
                                     weight_decay=0.001)
        loss_fn = nn.MSELoss()
        s = torch.from_numpy(sequence.copy())
        c = torch.from_numpy(context.copy())
        t = torch.from_numpy(((target - self.y_center_) / self.y_scale_)[:, None].copy())
        self.net_.train()
        for _ in range(self.epochs):
            optimizer.zero_grad()
            loss = loss_fn(self.net_(s, c), t)
            loss.backward()
            nn.utils.clip_grad_norm_(self.net_.parameters(), 1.0)
            optimizer.step()
        self.net_.eval()
        return self

    def predict(self, X):
        import torch

        sequence, context = self._inputs(X)
        with torch.no_grad():
            y = self.net_(torch.from_numpy(sequence.copy()),
                          torch.from_numpy(context.copy())).numpy().ravel()
        return y.astype(np.float64) * self.y_scale_ + self.y_center_


# Definido a nivel de módulo para permitir serialización de un candidato LSTM.
try:
    from torch import nn

    class _SequenceNet(nn.Module):
        def __init__(self, context_features: int, hidden_size: int):
            super().__init__()
            self.recurrent = nn.LSTM(input_size=1, hidden_size=hidden_size, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden_size + context_features, hidden_size),
                nn.ReLU(), nn.Linear(hidden_size, 1),
            )

        def forward(self, sequence, context):
            _, (hidden, _) = self.recurrent(sequence)
            return self.head(__import__("torch").cat([hidden[-1], context], dim=1))
except ImportError:
    # sklearn/RBF siguen disponibles cuando torch no está instalado.
    _SequenceNet = None
