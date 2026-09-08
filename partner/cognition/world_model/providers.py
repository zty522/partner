"""Replaceable hypothesis providers; neither provider scores its own truth."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

import numpy as np

from .hypotheses import FunctionHypothesis, hypothesis_catalog


class HypothesisProvider(Protocol):
    provider_id: str

    def propose(self, x: np.ndarray, y: np.ndarray, *, surprise: float) -> list[FunctionHypothesis]: ...


class LibraryHypothesisProvider:
    provider_id = "library_v1"

    def __init__(self, knowledge_level: str = "intermediate") -> None:
        if knowledge_level not in {"basic", "intermediate", "advanced"}:
            raise ValueError("knowledge_level must be basic, intermediate or advanced")
        self.knowledge_level = knowledge_level

    def propose(self, x: np.ndarray, y: np.ndarray, *, surprise: float) -> list[FunctionHypothesis]:
        catalog = hypothesis_catalog()
        basic = [row for row in catalog if row.hypothesis_id in {"constant", "linear", "quadratic"}]
        if self.knowledge_level == "basic" and len(x) < 6 and surprise < 0.20:
            return basic
        intermediate = [row for row in catalog if row.family in
                        {"constant", "polynomial", "fourier", "exp_decay"}]
        if self.knowledge_level != "advanced" and not (len(x) >= 10 and surprise >= 0.12):
            return intermediate
        return catalog


@dataclass
class _TrainingExample:
    points: np.ndarray
    label: int


class TransformerHypothesisProvider:
    """Tiny set Transformer trained to retrieve plausible function families.

    It proposes a bounded working set.  Independent BIC/MDL evidence in the
    engine still selects the winner; the Transformer never declares truth.
    """

    provider_id = "transformer_hypothesis_v1"
    FAMILIES = ("polynomial", "fourier", "exp_decay", "hinge")

    def __init__(self, *, seed: int = 17, d_model: int = 32) -> None:
        try:
            import torch
            from torch import nn
        except ImportError as exc:  # pragma: no cover
            raise ImportError("TransformerHypothesisProvider requires torch") from exc
        torch.manual_seed(seed)
        self._torch, self._nn, self.seed = torch, nn, int(seed)

        class Model(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.embed = nn.Sequential(nn.Linear(2, d_model), nn.GELU(), nn.Linear(d_model, d_model))
                layer = nn.TransformerEncoderLayer(d_model, 4, d_model * 2, dropout=0.0,
                                                   batch_first=True, norm_first=False)
                self.encoder = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
                self.query = nn.Parameter(torch.zeros(1, 1, d_model))
                self.head = nn.Linear(d_model, len(TransformerHypothesisProvider.FAMILIES))

            def forward(self, points, padding_mask=None):
                batch = points.shape[0]
                query = self.query.expand(batch, -1, -1)
                tokens = torch.cat((query, self.embed(points)), dim=1)
                if padding_mask is not None:
                    prefix = torch.zeros((batch, 1), dtype=torch.bool, device=points.device)
                    padding_mask = torch.cat((prefix, padding_mask), dim=1)
                encoded = self.encoder(tokens, src_key_padding_mask=padding_mask)
                return self.head(encoded[:, 0])

        self.model = Model()
        self.trained = False
        self.training_summary: dict[str, float | int] = {}

    @staticmethod
    def _normalize(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        xn = (x - np.min(x)) / max(float(np.max(x) - np.min(x)), 1e-8)
        yn = (y - np.mean(y)) / max(float(np.std(y)), 1e-8)
        return np.column_stack((xn, yn)).astype(np.float32)

    def _synthetic(self, count: int) -> list[_TrainingExample]:
        rng, rows = np.random.default_rng(self.seed), []
        for index in range(count):
            label, n = index % len(self.FAMILIES), int(rng.integers(9, 21))
            x = np.sort(rng.uniform(0, 1, n))
            if label == 0:
                y = rng.normal() + rng.uniform(-2, 2) * x + rng.uniform(-2, 2) * x ** 2
            elif label == 1:
                f = float(rng.choice([1.0, 2.0, 3.0]))
                y = rng.normal() + rng.uniform(0.8, 2.0) * np.sin(2 * np.pi * f * x + rng.uniform(-1, 1))
            elif label == 2:
                y = rng.normal() + rng.uniform(0.8, 2.0) * np.exp(-rng.uniform(0.8, 4.0) * x)
            else:
                knot = rng.uniform(0.25, 0.75)
                y = rng.normal() + rng.uniform(-1, 1) * x + rng.uniform(1.5, 3.0) * np.maximum(0, x - knot)
            y += rng.normal(0, 0.025, n)
            rows.append(_TrainingExample(self._normalize(x, y), label))
        return rows

    def fit_synthetic(self, *, examples: int = 320, epochs: int = 45,
                      lr: float = 0.003) -> dict[str, float | int]:
        torch, nn = self._torch, self._nn
        rows = self._synthetic(examples)
        max_len = max(len(row.points) for row in rows)
        data = np.zeros((len(rows), max_len, 2), dtype=np.float32)
        mask = np.ones((len(rows), max_len), dtype=bool)
        labels = np.zeros(len(rows), dtype=np.int64)
        for index, row in enumerate(rows):
            data[index, :len(row.points)] = row.points
            mask[index, :len(row.points)] = False
            labels[index] = row.label
        points = torch.from_numpy(data)
        padding = torch.from_numpy(mask)
        target = torch.from_numpy(labels)
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4)
        self.model.train()
        for _ in range(epochs):
            optimizer.zero_grad()
            loss = nn.functional.cross_entropy(self.model(points, padding), target)
            loss.backward()
            optimizer.step()
        self.model.eval()
        with torch.no_grad():
            logits = self.model(points, padding)
            accuracy = float((logits.argmax(1) == target).float().mean())
            final_loss = float(nn.functional.cross_entropy(logits, target))
        self.trained = True
        self.training_summary = {"examples": examples, "epochs": epochs,
                                 "training_accuracy": accuracy, "training_loss": final_loss}
        return dict(self.training_summary)

    def family_probabilities(self, x: np.ndarray, y: np.ndarray) -> dict[str, float]:
        if not self.trained:
            raise RuntimeError("TransformerHypothesisProvider must be fitted before use")
        points = self._torch.from_numpy(self._normalize(np.asarray(x), np.asarray(y)))[None, :, :]
        self.model.eval()
        with self._torch.no_grad():
            probs = self._torch.softmax(self.model(points), dim=-1)[0].cpu().numpy()
        return {name: float(value) for name, value in zip(self.FAMILIES, probs)}

    def propose(self, x: np.ndarray, y: np.ndarray, *, surprise: float) -> list[FunctionHypothesis]:
        probabilities = self.family_probabilities(x, y)
        ranked = sorted(probabilities.items(), key=lambda row: row[1], reverse=True)
        entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities.values())
        normalized_entropy = entropy / math.log(len(self.FAMILIES))
        # Sparse or uncertain observations need broader recall.  A confident
        # neural guess may reduce work, but it may never erase all alternatives.
        top_k = 2
        if len(x) < 9 or surprise >= 0.18:
            top_k = len(self.FAMILIES)
        elif ranked[0][1] < 0.75 or normalized_entropy > 0.62:
            top_k = 3
        selected = {name for name, _ in ranked[:top_k]}
        self.last_diagnostics = {
            "family_probabilities": probabilities,
            "family_entropy_normalized": float(normalized_entropy),
            "selected_families": [name for name, _ in ranked[:top_k]],
            "top_k": top_k,
        }
        # Always retain simple baselines so neural recall cannot suppress an
        # Occam alternative or make the evaluator circular.
        rows = [row for row in hypothesis_catalog(origin="transformer_proposed")
                if row.hypothesis_id in {"constant", "linear", "quadratic"} or row.family in selected]
        return rows
