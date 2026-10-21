"""Typed error taxonomy. Everything the pipeline surfaces maps to one of these."""

from __future__ import annotations


class TradeMindError(Exception):
    code = "trademind_error"


class ContractViolation(TradeMindError):
    code = "contract_violation"


class NotFresh(TradeMindError):
    code = "not_fresh"


class StaleThesis(NotFresh):
    code = "stale_thesis"


class ProviderError(TradeMindError):
    code = "provider_error"


class ProviderTimeout(ProviderError):
    code = "provider_timeout"


class ProviderRefused(ProviderError):
    code = "provider_refused"


class RateLimited(ProviderError):
    code = "rate_limited"


class CircuitOpen(ProviderError):
    code = "circuit_open"


class GuardVeto(TradeMindError):
    code = "guard_veto"

    def __init__(self, reason_codes: list[str], message: str = "") -> None:
        self.reason_codes = reason_codes
        super().__init__(message or f"guard veto: {','.join(reason_codes)}")


class InsufficientFunds(TradeMindError):
    code = "insufficient_funds"


class DuplicateKey(TradeMindError):
    code = "duplicate_key"


class MarketClosed(TradeMindError):
    code = "market_closed"


class DataUnavailable(TradeMindError):
    code = "data_unavailable"


class CalendarCoverageError(TradeMindError):
    """The requested date lies outside the calendar's verified coverage;
    its trading status is unknown, never assumed closed or open."""
    code = "calendar_coverage"


class ConfigError(TradeMindError):
    code = "config_error"


class SchemaVersionError(TradeMindError):
    """The database was written by a newer program (schema version ahead of
    this one); it is refused before any structural or account write."""
    code = "schema_version"


class RecoveryRequired(TradeMindError):
    code = "recovery_required"
