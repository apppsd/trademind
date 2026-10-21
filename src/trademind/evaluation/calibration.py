"""Calibration metrics (TASK-20): Brier, log loss, ECE, reliability bins.

Only probabilities whose declared semantics matches the preregistered label
semantics enter calibration; refusals/timeouts/unmatured samples are counted
separately and never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import exp, log


@dataclass
class CalibrationReport:
    question_id: str
    n_scored: int = 0
    n_excluded_refused: int = 0
    n_excluded_unmatured: int = 0
    n_excluded_semantics: int = 0
    brier: float | None = None
    log_loss: float | None = None
    ece: float | None = None
    bins: list[dict] = field(default_factory=list)
    undefined_reason: str | None = None


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]], eps: float = 1e-12) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total += -(log(p) if y == 1 else log(1 - p))
    return total / len(pairs)


def reliability_bins(pairs: list[tuple[float, int]], n_bins: int = 5) -> tuple[list[dict], float | None]:
    """Equal-width bins on [0,1]; returns bins and ECE."""
    if not pairs:
        return [], None
    bins: list[dict] = [
        {"range": [i / n_bins, (i + 1) / n_bins], "n": 0, "avg_p": 0.0, "avg_y": 0.0}
        for i in range(n_bins)
    ]
    for p, y in pairs:
        idx = min(int(p * n_bins), n_bins - 1)
        b = bins[idx]
        b["n"] += 1
        b["avg_p"] += p
        b["avg_y"] += y
    ece = 0.0
    total = len(pairs)
    for b in bins:
        if b["n"]:
            b["avg_p"] = round(b["avg_p"] / b["n"], 6)
            b["avg_y"] = round(b["avg_y"] / b["n"], 6)
            ece += (b["n"] / total) * abs(b["avg_p"] - b["avg_y"])
        b["range"] = [round(b["range"][0], 4), round(b["range"][1], 4)]
    return bins, round(ece, 6)


def calibrate(samples: list[dict], question_id: str,
              label_semantics: str = "calibrated_event_probability") -> CalibrationReport:
    """samples: [{"question_id","probability","semantics","outcome"(0/1)|None,"refused"}]"""
    report = CalibrationReport(question_id=question_id)
    pairs: list[tuple[float, int]] = []
    for s in samples:
        if s.get("question_id") != question_id:
            continue
        if s.get("refused"):
            report.n_excluded_refused += 1
            continue
        if s.get("outcome") is None:
            report.n_excluded_unmatured += 1
            continue
        if s.get("semantics") != label_semantics:
            report.n_excluded_semantics += 1
            continue
        pairs.append((float(s["probability"]), int(s["outcome"])))
    report.n_scored = len(pairs)
    if report.n_scored == 0:
        report.undefined_reason = "no scoreable samples"
        return report
    report.brier = round(brier_score(pairs), 6)
    report.log_loss = round(log_loss(pairs), 6)
    report.bins, report.ece = reliability_bins(pairs)
    return report


class CostReport:
    """Model cost/latency from model_calls rows (TASK-20)."""

    @staticmethod
    def from_calls(calls: list[dict]) -> dict:
        if not calls:
            return {"n": 0}
        lat = sorted(int(c.get("latency_ms") or 0) for c in calls)

        def pct(p):
            i = min(len(lat) - 1, int(len(lat) * p / 100))
            return lat[i]

        by_role: dict[str, dict] = {}
        tokens_in = tokens_out = 0
        for c in calls:
            role = c.get("role", "?")
            r = by_role.setdefault(role, {"n": 0, "p50_ms": 0, "p95_ms": 0,
                                          "tokens_in": 0, "tokens_out": 0, "stub": 0})
            r["n"] += 1
            r["stub"] += 1 if c.get("is_stub") else 0
            r["tokens_in"] += int(c.get("input_tokens") or 0)
            r["tokens_out"] += int(c.get("output_tokens") or 0)
            tokens_in += int(c.get("input_tokens") or 0)
            tokens_out += int(c.get("output_tokens") or 0)
        for role, r in by_role.items():
            role_lat = sorted(int(c.get("latency_ms") or 0) for c in calls
                              if c.get("role") == role)
            r["p50_ms"] = role_lat[len(role_lat) // 2]
            r["p95_ms"] = role_lat[min(len(role_lat) - 1, int(len(role_lat) * 0.95))]
        return {
            "n_calls": len(calls),
            "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_note": "stub calls carry no vendor cost; real cost needs usage data",
            "by_role": by_role,
        }
