"""Typed error taxonomy. Everything the pipeline surfaces maps to one of these."""

from __future__ import annotations


class TradeMindError(Exception):
    code = "trademind_error"


class ContractViolation(TradeMindError):
    code = "contract_violation"


class NotFresh(TradeMindError):
    code = "not_fresh"


# Closed public subtype whitelist for the NotFresh / data-reject reason
# channel (review R2, round 3): public summaries, intraday health and the
# daily reports may publish only these constants plus type-checked
# structured parameters — never a raw exception body. The two extra
# constants cover reasons already living on the same channel: the legacy
# merged minute-context subtype on historical audit rows and the
# WAIT_DATA_QUALITY feature gate.
NOT_FRESH_PUBLIC_REASONS = frozenset({
    'missing visible minute bars',
    'stale visible minute context',
    'no fresh visible minute quote',
    'minute reference quote quality validation failed',
    'trading calendar coverage insufficient',
    'no reference price',
    'async model result expired or account changed',
    'async strategy conditions changed before execution',
    'context as_of is in the future',
    'account contains future events; historical decisions require a fresh ReplayRunner',
    'missing or stale visible minute context',  # legacy merged subtype (history)
    'incomplete minute features',  # WAIT_DATA_QUALITY gate on the same channel
    'unknown',  # fallback; never carries exception text
})

# Legacy raise sites embedded dynamic details in the message (instrument id,
# wrapped calendar error). Those stable prefixes map onto the closed
# constants; the structured details live in dedicated audit fields instead.
NOT_FRESH_REASON_PREFIXES = (
    ('no fresh visible minute quote', 'no fresh visible minute quote'),
    ('trading calendar coverage insufficient', 'trading calendar coverage insufficient'),
    ('no reference price', 'no reference price'),
)

UNKNOWN_REASON = 'unknown'


def classify_not_fresh_reason(value) -> str:
    """Map a data-reject reason onto one of ``NOT_FRESH_PUBLIC_REASONS``.

    Exact constants pass through; legacy dynamic strings match by stable
    prefix; anything else — including arbitrary exception bodies — collapses
    to ``'unknown'``. Non-string input can never leak."""
    if not isinstance(value, str):
        return UNKNOWN_REASON
    if value in NOT_FRESH_PUBLIC_REASONS:
        return value
    for prefix, constant in NOT_FRESH_REASON_PREFIXES:
        if value.startswith(prefix):
            return constant
    return UNKNOWN_REASON


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
