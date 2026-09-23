"""A small, auditable latent-space dynamics model for shadow forecasting.

This is deliberately a linear baseline: PCA supplies the latent state and ridge
regression learns z(t+1) and observed gain from z(t) plus a deterministic action
embedding.  It abstains with sparse or out-of-distribution data.  The model never
executes an action and is non-authoritative in Core v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
import json
import math

from .models import CandidateAction, DecisionState, Forecast, digest


ACTION_DIM = 8
MIN_SAMPLES = 8


@dataclass(frozen=True)
class TransitionSample:
    state: Mapping[str, float]
    action: CandidateAction
    next_state: Mapping[str, float]
    gain: float


def action_embedding(candidate: CandidateAction) -> list[float]:
    values = [0.0] * ACTION_DIM
    tokens = [candidate.event_type, candidate.description, candidate.risk,
              *[str(k) for k in sorted(candidate.parameters)]]
    for token in tokens:
        raw = sha256(token.encode("utf-8")).digest()
        values[raw[0] % ACTION_DIM] += (-1.0 if raw[1] & 1 else 1.0) * (0.5 + raw[2] / 510)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class LatentDynamicsModel:
    def __init__(self, artifact: Mapping[str, Any] | None = None) -> None:
        self.artifact = dict(artifact or {})

    @property
    def fitted(self) -> bool:
        return int(self.artifact.get("training_count") or 0) >= MIN_SAMPLES

    @classmethod
    def fit(cls, samples: Sequence[TransitionSample], *, latent_dim: int = 4,
            ridge: float = 1e-3) -> "LatentDynamicsModel":
        if len(samples) < MIN_SAMPLES:
            return cls({"schema_version": 1, "training_count": len(samples),
                        "status": "insufficient_data", "minimum_samples": MIN_SAMPLES})
        import numpy as np
        names = sorted({k for row in samples for k in (*row.state.keys(), *row.next_state.keys())})
        x = np.asarray([[float(row.state.get(k, 0.0)) for k in names] for row in samples])
        y = np.asarray([[float(row.next_state.get(k, 0.0)) for k in names] for row in samples])
        mean = x.mean(axis=0)
        scale = x.std(axis=0)
        scale[scale < 1e-8] = 1.0
        xn, yn = (x - mean) / scale, (y - mean) / scale
        _, _, vt = np.linalg.svd(xn, full_matrices=False)
        dimensions = max(1, min(int(latent_dim), vt.shape[0], vt.shape[1]))
        components = vt[:dimensions]
        z, z_next = xn @ components.T, yn @ components.T
        action = np.asarray([action_embedding(row.action) for row in samples])
        design = np.column_stack((np.ones(len(samples)), z, action))
        inverse = np.linalg.pinv(design.T @ design + ridge * np.eye(design.shape[1]))
        transition = inverse @ design.T @ z_next
        gains = np.asarray([float(row.gain) for row in samples])
        gain_coef = inverse @ design.T @ gains
        predicted_z = design @ transition
        predicted_gain = design @ gain_coef
        residual = np.sqrt(np.mean((z_next - predicted_z) ** 2, axis=1)
                           + (gains - predicted_gain) ** 2)
        distances = np.sqrt(np.sum(z * z, axis=1))
        artifact = {
            "schema_version": 1, "status": "fitted", "training_count": len(samples),
            "feature_names": names, "mean": mean.tolist(), "scale": scale.tolist(),
            "components": components.tolist(), "transition_coef": transition.tolist(),
            "gain_coef": gain_coef.tolist(), "residual_std": float(residual.std() + residual.mean()),
            "ood_limit": float(max(1.0, np.quantile(distances, 0.95) * 1.25)),
        }
        artifact["model_version"] = f"latent_linear_{digest(artifact)[:12]}"
        return cls(artifact)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> "LatentDynamicsModel":
        target = Path(path)
        if not target.is_file():
            return cls()
        return cls(json.loads(target.read_text(encoding="utf-8")))

    def forecast(self, state: DecisionState, candidate: CandidateAction) -> Forecast:
        if not self.fitted:
            return Forecast(candidate.candidate_id, "abstained", reason="insufficient trusted transitions",
                            model_version=str(self.artifact.get("model_version") or ""))
        import numpy as np
        names = self.artifact["feature_names"]
        mean, scale = np.asarray(self.artifact["mean"]), np.asarray(self.artifact["scale"])
        components = np.asarray(self.artifact["components"])
        x = np.asarray([float(state.numeric_features.get(k, 0.0)) for k in names])
        z = ((x - mean) / scale) @ components.T
        distance = float(np.sqrt(np.sum(z * z)))
        if distance > float(self.artifact["ood_limit"]):
            return Forecast(candidate.candidate_id, "abstained", uncertainty=1.0,
                            ood_distance=distance, model_version=self.artifact["model_version"],
                            reason="state is outside the trained latent support")
        design = np.asarray([1.0, *z.tolist(), *action_embedding(candidate)])
        next_z = design @ np.asarray(self.artifact["transition_coef"])
        next_scaled = next_z @ components
        next_state = next_scaled * scale + mean
        gain = float(design @ np.asarray(self.artifact["gain_coef"]))
        uncertainty = min(1.0, float(self.artifact["residual_std"]) * (1 + distance / self.artifact["ood_limit"]))
        probability = 1.0 / (1.0 + math.exp(-gain / max(uncertainty, 0.05)))
        return Forecast(candidate.candidate_id, "predicted", expected_gain=gain,
                        success_probability=probability, uncertainty=uncertainty,
                        ood_distance=distance,
                        predicted_next_features={k: float(v) for k, v in zip(names, next_state)},
                        model_version=self.artifact["model_version"], authoritative=False,
                        reason="shadow PCA plus ridge latent dynamics forecast")
