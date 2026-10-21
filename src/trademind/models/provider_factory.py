"""Provider factory from AppConfig (TASK-11)."""

from __future__ import annotations

from ..config import AppConfig
from ..errors import ConfigError
from ..storage import Repository
from .base import Budget, CallAuditor, LLMProvider, JEVProvider
from .deepseek import DeepSeekProvider
from .jev import TypeSafeJEVProvider
from .stub import HttpJEVProvider, StubJEVProvider, StubLLMProvider


def build_llm(cfg: AppConfig, repo: Repository) -> tuple[LLMProvider, bool]:
    """Returns (provider, is_stub). auto: real key -> DeepSeek, else stub."""
    auditor = CallAuditor(repo)
    budget = Budget(limit=cfg.llm.daily_call_budget)
    if cfg.t0.enabled:
        from .daily_budget import DailyModelBudget
        budget = DailyModelBudget(repo, cfg.t0, cfg.account_id)
    provider = cfg.llm.provider
    if provider == "stub":
        return StubLLMProvider(auditor=auditor, budget=budget), True
    if provider == "deepseek":
        if not cfg.llm.api_key:
            raise ConfigError("llm.provider=deepseek but api_key missing")
        return DeepSeekProvider(
            api_key=cfg.llm.api_key, model=cfg.llm.model,
            base_url=cfg.llm.base_url, auditor=auditor,
            temperature=cfg.llm.temperature,
            max_repair_attempts=cfg.llm.max_repair_attempts,
            budget=budget,
        ), False
    if provider == "auto":
        if cfg.llm.api_key:
            return DeepSeekProvider(
                api_key=cfg.llm.api_key, model=cfg.llm.model,
                base_url=cfg.llm.base_url, auditor=auditor,
                temperature=cfg.llm.temperature,
                max_repair_attempts=cfg.llm.max_repair_attempts,
                budget=budget,
            ), False
        return StubLLMProvider(auditor=auditor, budget=budget), True
    raise ConfigError(f"unknown llm.provider {provider!r}")


def build_jev(cfg: AppConfig, repo: Repository) -> tuple[JEVProvider, bool]:
    auditor = CallAuditor(repo)
    provider = cfg.jev.provider
    budget = Budget(limit=cfg.jev.batch_limit * 100)
    if cfg.t0.enabled:
        from .daily_budget import DailyModelBudget
        budget = DailyModelBudget(repo, cfg.t0, cfg.account_id)
    if provider == "stub":
        return StubJEVProvider(question_version=cfg.jev.question_version,
                               auditor=auditor), True
    if provider in ("jev_typesafe", "typesafe"):
        if not cfg.jev.api_key:
            raise ConfigError("jev.provider=jev_typesafe but api_key missing")
        return TypeSafeJEVProvider(
            base_url=cfg.jev.base_url or "https://api.typesafe.ai",
            api_key=cfg.jev.api_key,
            model=cfg.jev.model or "jev-latest",
            question_version=cfg.jev.question_version,
            timeout_s=float(cfg.jev.timeout_s),
            auditor=auditor,
            budget=budget,
        ), False
    if provider == "http":
        return HttpJEVProvider(
            base_url=cfg.jev.base_url, api_key=cfg.jev.api_key,
            model=cfg.jev.model, question_version=cfg.jev.question_version,
            timeout_s=float(cfg.jev.timeout_s), auditor=auditor,
        ), False
    if provider == "auto":
        if cfg.jev.api_key:
            return TypeSafeJEVProvider(
                base_url=cfg.jev.base_url or "https://api.typesafe.ai",
                api_key=cfg.jev.api_key,
                model=cfg.jev.model or "jev-latest",
                question_version=cfg.jev.question_version,
                timeout_s=float(cfg.jev.timeout_s),
                auditor=auditor,
                budget=budget,
            ), False
        return StubJEVProvider(question_version=cfg.jev.question_version, auditor=auditor), True
    raise ConfigError(f"unknown jev.provider {cfg.jev.provider!r}")
