"""Configuration loading: TOML file + local .env + environment overrides.

Mode isolation (TASK-06 acceptance): replay never loads trading credentials.
Credentials only come from environment variables, root .env or config/trademind.toml
(gitignored); the example file carries empty placeholders only.
"""

from __future__ import annotations

import os
import tomllib
from dotenv import dotenv_values
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "trademind.toml"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "trademind.example.toml"
ENV_FILE_PATH = REPO_ROOT / ".env"

VALID_MODES = {"paper", "shadow", "replay", "live"}


@dataclass
class LLMConfig:
    provider: str = "auto"  # auto | deepseek | stub
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"  # verify at runtime with a probe; see research
    temperature: float = 0.2
    timeout_s: int = 60
    max_repair_attempts: int = 1
    max_concurrent: int = 4
    daily_call_budget: int = 500
    capability_verified: bool = False


@dataclass
class JEVConfig:
    provider: str = "stub"  # stub | http
    base_url: str = ""  # JEV endpoint unknown until user supplies it (ISSUE-02)
    api_key: str = ""
    model: str = ""
    timeout_s: int = 10
    batch_limit: int = 8
    question_version: str = "qv-2026-10-07"
    capability_verified: bool = False


@dataclass
class RiskConfig:
    max_position_weight: Decimal = Decimal("0.20")
    max_total_exposure: Decimal = Decimal("0.95")
    max_daily_loss: Decimal = Decimal("0.03")
    max_order_notional: Decimal = Decimal("200000")
    price_tolerance: Decimal = Decimal("0.02")
    intent_ttl_seconds: int = 300
    context_ttl_seconds: int = 900
    quote_max_age_seconds: int = 86400 * 3  # EOD daily bars by default
    max_open_positions: int = 10
    max_orders_per_day: int = 40
    policy_version: str = "risk-2026-10-07"


@dataclass
class PaperConfig:
    initial_cash: Decimal = Decimal("1000000")
    currency: str = "CNY"
    commission_rate: Decimal = Decimal("0.00025")
    commission_min: Decimal = Decimal("5")
    stamp_duty_rate_sell: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    slippage_bps: int = 5
    fill_lag_bars: int = 1  # fill at next visible bar after decision


@dataclass
class DataConfig:
    fixtures_dir: str = ""
    provider_pref: list[str] = field(default_factory=lambda: ["fixture", "akshare", "baostock", "tushare"])
    tushare_token: str = ""
    # Minute quality routing caps (ARCH-020 / TASK-20261014-04); market-routing
    # budgets only — model budgets are configured under [t0].
    minute_source_timeout_seconds: float = 10.0
    minute_max_sources: int = 3
    minute_round_timeout_seconds: float = 30.0
    minute_failure_backoff_seconds: float = 60.0
    minute_max_backoff_seconds: float = 900.0
    minute_daily_request_limit: int = 4000
    minute_cross_check_enabled: bool = False
    minute_cross_check_interval_seconds: float = 900.0
    minute_cross_check_max_daily: int = 24
    minute_cross_check_tolerance_ratio: str = "0.005"


@dataclass
class StorageConfig:
    url: str = "sqlite:///data/runtime/trademind.db"


@dataclass
class APIConfig:
    host: str = "127.0.0.1"
    port: int = 8321
    token: str = ""  # control-plane token; empty disables mutating endpoints


@dataclass
class T0ExperimentConfig:
    enabled: bool = False
    profile: str = "jev_deepseek"  # jev_deepseek
    initial_cash: Decimal = Decimal("500")
    max_total_exposure: Decimal = Decimal("300")
    max_single_exposure: Decimal = Decimal("200")
    min_cash_reserve: Decimal = Decimal("100")
    stop_loss_ratio: Decimal = Decimal("0.008")  # 0.8%
    max_daily_loss_ratio: Decimal = Decimal("0.03")  # 3%
    consecutive_losses_limit: int = 3
    daily_new_orders_limit: int = 10
    daily_model_cost_limit_cny: Decimal = Decimal("5.0")
    daily_decision_call_budget: int = 100
    candidate_cooldown_seconds: int = 300
    quote_eval_interval_seconds: int = 60
    close_out_time: str = "14:55"
    no_new_entry_time: str = "14:50"
    whitelist: list[str] = field(default_factory=lambda: ["513050.SH", "513100.SH", "518880.SH"])
    # Operator-verified current CNY prices; missing prices block paid requests.
    model_input_cny_per_million: Decimal | None = None
    model_output_cny_per_million: Decimal | None = None
    model_max_output_tokens: int = 1024
    jev_input_cny_per_million: Decimal | None = None
    jev_output_cny_per_million: Decimal | None = None
    jev_output_token_reservation: int = 4096
    strategy_refresh_seconds: int = 900
    strategy_ttl_seconds: int = 1200
    uncertain_refresh_seconds: int = 300
    fast_eval_seconds: int = 300
    strategy_request_limit: int = 30
    jev_request_limit: int = 60
    probe_request_limit: int = 10
    action_confidence_floor: Decimal = Decimal('0.75')
    evidence_yes_floor: Decimal = Decimal('0.80')



@dataclass
class MarketAccessConfig:
    # Empty means not confirmed, never inferred from a quote endpoint.
    confirmed_permissions: list[str] = field(default_factory=list)
    cash_cny: Decimal = Decimal('500')
    cash_hkd: Decimal = Decimal('0')
    reserve_cny: Decimal = Decimal('100')
    reserve_hkd: Decimal = Decimal('0')
    markets: list[str] = field(default_factory=lambda: ['etf', 'lof', 'convertible_bond', 'hk_stock', 'futures'])
    rules_file: str = ''
    request_timeout_seconds: int = 12
    scan_timeout_seconds: int = 60


@dataclass
class AppConfig:
    mode: str = "paper"
    account_id: str = "paper-001"
    universe_version: str = "univ-2026-10-07"
    universe: list[str] = field(
        default_factory=lambda: [
            "600519.SH",
            "000001.SZ",
            "601318.SH",
            "600036.SH",
            "000858.SZ",
        ]
    )
    llm: LLMConfig = field(default_factory=LLMConfig)
    jev: JEVConfig = field(default_factory=JEVConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    data: DataConfig = field(default_factory=DataConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: APIConfig = field(default_factory=APIConfig)
    t0: T0ExperimentConfig = field(default_factory=T0ExperimentConfig)
    market_access: MarketAccessConfig = field(default_factory=MarketAccessConfig)
    source: str = "defaults"

    @property
    def is_replay(self) -> bool:
        return self.mode == "replay"


def _apply(obj: object, section: dict) -> None:
    for k, v in section.items():
        if not hasattr(obj, k):
            raise ConfigError(f"unknown config key: {k}")
        cur = getattr(obj, k)
        if isinstance(cur, Decimal) or k in ('model_input_cny_per_million', 'model_output_cny_per_million', 'jev_input_cny_per_million', 'jev_output_cny_per_million'):
            setattr(obj, k, Decimal(str(v)))
        elif isinstance(cur, bool) or cur is None:
            setattr(obj, k, v)
        elif isinstance(cur, int) and not isinstance(v, bool):
            setattr(obj, k, int(v))
        elif isinstance(cur, float):
            setattr(obj, k, float(v))
        elif isinstance(cur, list):
            setattr(obj, k, list(v))
        else:
            setattr(obj, k, str(v))


def load_config(path: str | Path | None = None, *, load_credentials: bool = True) -> AppConfig:
    cfg = AppConfig()
    p = Path(path) if path else (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH)
    if p.exists():
        with open(p, "rb") as f:
            raw = tomllib.load(f)
        cfg.mode = str(raw.get("mode", cfg.mode))
        cfg.account_id = str(raw.get("account_id", cfg.account_id))
        cfg.universe_version = str(raw.get("universe_version", cfg.universe_version))
        if "universe" in raw:
            cfg.universe = [str(x) for x in raw["universe"]]
        sections = {
            "llm": cfg.llm,
            "jev": cfg.jev,
            "risk": cfg.risk,
            "paper": cfg.paper,
            "data": cfg.data,
            "storage": cfg.storage,
            "api": cfg.api,
            "t0": cfg.t0,
            "market_access": cfg.market_access,
        }
        for name, obj in sections.items():
            if name in raw:
                _apply(obj, raw[name])
        if cfg.t0.enabled:
            # Sync paper settings if not explicitly configured in raw
            if "paper" not in raw or "initial_cash" not in raw["paper"]:
                cfg.paper.initial_cash = cfg.t0.initial_cash
            if "universe" not in raw and cfg.t0.whitelist:
                cfg.universe = list(cfg.t0.whitelist)
            # Enforce T0 budget invariants
            if cfg.t0.min_cash_reserve + cfg.t0.max_single_exposure > cfg.t0.initial_cash:
                raise ConfigError(
                    f"T0 budget invalid: min_cash_reserve ({cfg.t0.min_cash_reserve}) + "
                    f"max_single_exposure ({cfg.t0.max_single_exposure}) exceeds "
                    f"initial_cash ({cfg.t0.initial_cash})"
                )
            if cfg.t0.profile in ("jev_deepseek", "jev_fast_decision") and cfg.llm.model != "deepseek-flash":
                raise ConfigError(
                    f"T0 profile jev_deepseek requires llm.model='deepseek-flash', got {cfg.llm.model!r}"
                )
        cfg.source = str(p)
    if load_credentials:
        _apply_env(cfg)
    else:
        cfg.llm.api_key = cfg.jev.api_key = cfg.api.token = cfg.data.tushare_token = ''
    _validate_mode_isolation(cfg)
    return cfg


def _apply_env(cfg: AppConfig) -> None:
    # Read without mutating os.environ; explicit process values take precedence.
    values = {k: v for k, v in dotenv_values(ENV_FILE_PATH, interpolate=False).items()
              if v is not None}
    values.update(os.environ)

    def env(name: str, default: str = "") -> str:
        return values.get(name, default)

    if key := env("TRADEMIND_DEEPSEEK_API_KEY"):
        cfg.llm.api_key = key
    if key := env("DEEPSEEK_API_KEY"):
        cfg.llm.api_key = key
    if v := env("TRADEMIND_LLM_MODEL"):
        cfg.llm.model = v
    if v := env("TRADEMIND_LLM_BASE_URL"):
        cfg.llm.base_url = v
    if key := env("TRADEMIND_JEV_API_KEY"):
        cfg.jev.api_key = key
    if v := env("TRADEMIND_JEV_BASE_URL"):
        cfg.jev.base_url = v
    if v := env("TRADEMIND_TUSHARE_TOKEN"):
        cfg.data.tushare_token = v
    if v := env("TRADEMIND_MODE"):
        cfg.mode = v
    if v := env("TRADEMIND_API_TOKEN"):
        cfg.api.token = v
    if v := env("TRADEMIND_STORAGE_URL"):
        cfg.storage.url = v


def _validate_mode_isolation(cfg: AppConfig) -> None:
    if cfg.t0.enabled:
        if cfg.t0.profile not in ('jev_deepseek', 'jev_fast_decision'):
            raise ConfigError('unknown T0 profile')
        if cfg.t0.profile == 'jev_fast_decision':
            intervals = (cfg.t0.strategy_refresh_seconds, cfg.t0.strategy_ttl_seconds,
                         cfg.t0.uncertain_refresh_seconds, cfg.t0.fast_eval_seconds)
            if any(v <= 0 for v in intervals) or cfg.t0.strategy_ttl_seconds > 1200:
                raise ConfigError('invalid fast strategy cadence/TTL')
            if cfg.t0.strategy_refresh_seconds > cfg.t0.strategy_ttl_seconds:
                raise ConfigError('refresh interval exceeds plan TTL')
            if cfg.account_id in ('paper-t0','paper-default'):
                raise ConfigError('fast profile requires an isolated account_id')
            if cfg.t0.jev_output_token_reservation <= 0:
                raise ConfigError('invalid JEV output reservation')
            if not 0 <= cfg.t0.action_confidence_floor <= 1 or not 0 <= cfg.t0.evidence_yes_floor <= 1:
                raise ConfigError('invalid JEV thresholds')
        if cfg.llm.model != "deepseek-flash":
            raise ConfigError("T0 requires llm.model='deepseek-flash'")
        cfg.paper.initial_cash = cfg.t0.initial_cash
        if cfg.t0.model_max_output_tokens <= 0:
            raise ConfigError("model_max_output_tokens must be positive")
    if cfg.mode not in VALID_MODES:
        raise ConfigError(f"invalid mode {cfg.mode!r}; one of {sorted(VALID_MODES)}")
    if cfg.mode == "replay" and (cfg.data.tushare_token or cfg.api.token or cfg.llm.api_key or cfg.jev.api_key
                                 or cfg.llm.provider not in ("auto", "stub") or cfg.jev.provider != "stub"
                                 or cfg.data.provider_pref != ["fixture"]):
        raise ConfigError(
            "replay mode must not load trading/provider credentials "
            "(TRADEMIND_TUSHARE_TOKEN / TRADEMIND_API_TOKEN set)"
        )
    if cfg.mode == "live":
        raise ConfigError(
            "live mode has no execution channel in V1; use paper/shadow/replay"
        )
