"""Partner-owned copy of the accepted differentiable FunctionPool organ."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class LinearKernel(nn.Module):
    def __init__(self, in_dim: int, out_dim: int = 1) -> None:
        super().__init__(); self.linear = nn.Linear(in_dim, out_dim)
    def forward(self, x): return self.linear(x)


class QuadraticKernel(nn.Module):
    def __init__(self, in_dim: int, out_dim: int = 1) -> None:
        super().__init__(); self.A = nn.Parameter(torch.randn(in_dim, in_dim) * 0.01)
        self.linear = nn.Linear(in_dim, out_dim)
    def forward(self, x):
        return self.linear(x) + torch.einsum("bi,ij,bj->b", x, self.A, x).unsqueeze(-1)


class FourierKernel(nn.Module):
    def __init__(self, in_dim: int, num_freqs: int = 8, out_dim: int = 1) -> None:
        super().__init__(); self.freqs = nn.Parameter(torch.randn(num_freqs, in_dim) * 0.1)
        self.phases = nn.Parameter(torch.zeros(num_freqs, in_dim))
        self.amplitudes = nn.Parameter(torch.ones(num_freqs, out_dim) * 0.1)
    def forward(self, x):
        values = torch.sin(self.freqs.unsqueeze(0) * x.unsqueeze(1) + self.phases.unsqueeze(0)).sum(-1)
        return (values.unsqueeze(-1) * self.amplitudes).sum(1, keepdim=True).squeeze(-1)


class ExponentialDecayKernel(nn.Module):
    def __init__(self, in_dim: int, out_dim: int = 1) -> None:
        super().__init__(); self.amplitude = nn.Parameter(torch.ones(1))
        self.decay_rate = nn.Parameter(torch.ones(in_dim) * 0.5); self.linear = nn.Linear(in_dim, out_dim)
    def forward(self, x):
        return self.amplitude * torch.exp(-self.decay_rate.unsqueeze(0) * x).sum(-1, keepdim=True) + self.linear(x)


class FunctionPool(nn.Module):
    KERNEL_NAMES = ["linear", "quadratic", "fourier", "expdecay"]
    def __init__(self, in_dim: int, num_kernels: int = 4) -> None:
        super().__init__(); self.in_dim, self.num_kernels = in_dim, num_kernels
        self.kernels = nn.ModuleList([LinearKernel(in_dim), QuadraticKernel(in_dim),
                                      FourierKernel(in_dim), ExponentialDecayKernel(in_dim)][:num_kernels])
        self.gate = nn.Sequential(nn.Linear(in_dim, 16), nn.ReLU(), nn.Linear(16, num_kernels))
    def forward(self, x, kernel_mask=None):
        outputs = [kernel(x) if kernel_mask is None or kernel_mask[index]
                   else torch.zeros(x.shape[0], 1, device=x.device)
                   for index, kernel in enumerate(self.kernels)]
        outputs = torch.stack(outputs, dim=1)
        logits = self.gate(x)
        if kernel_mask is not None:
            mask = torch.tensor(kernel_mask, dtype=torch.bool, device=x.device).unsqueeze(0).expand_as(logits)
            logits = logits.masked_fill(~mask, float("-inf"))
        probabilities = torch.nan_to_num(F.softmax(logits, dim=-1), nan=0.0)
        return (outputs * probabilities.unsqueeze(-1)).sum(dim=1), probabilities


class ComplexityPenalty(nn.Module):
    def __init__(self, weight: float = 0.01) -> None:
        super().__init__(); self.weight = weight
    def forward(self, probabilities):
        return self.weight * (-(probabilities * torch.log(probabilities + 1e-8)).sum(-1).mean())
