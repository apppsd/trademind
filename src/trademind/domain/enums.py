"""All enumerations. Unknown actions must fail schema validation, never coerce."""

from enum import Enum


class Market(str, Enum):
    """Exchanges across mainland, HK and futures markets."""

    XSHG = "XSHG"  # Shanghai
    XSHE = "XSHE"  # Shenzhen
    XHKG = "XHKG"  # Hong Kong
    XSGE = "XSGE"  # Shanghai Futures
    XDCE = "XDCE"  # Dalian Commodity
    XZCE = "XZCE"  # Zhengzhou Commodity
    CCFX = "CCFX"  # China Financial Futures
    XINE = "XINE"  # Shanghai International Energy


class RunMode(str, Enum):
    PAPER = "paper"
    SHADOW = "shadow"
    REPLAY = "replay"
    LIVE = "live"  # no execution channel in V1; guard vetoes


class Direction(str, Enum):
    LONG = "LONG"
    NEUTRAL = "NEUTRAL"
    AVOID = "AVOID"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"


class GuardVerdict(str, Enum):
    ALLOW = "ALLOW"
    VETO = "VETO"
    REPLAN = "REPLAN"


class RiskVerdict(str, Enum):
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    REJECT = "REJECT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class IntentStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ThesisDirection(str, Enum):
    LONG = "LONG"
    NEUTRAL = "NEUTRAL"
    AVOID = "AVOID"


class MemoryType(str, Enum):
    EVENT = "EVENT"
    TRADE = "TRADE"
    LESSON = "LESSON"
    PATTERN = "PATTERN"


class MemoryReviewState(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"
    RETRACTED = "RETRACTED"
    EXPIRED = "EXPIRED"


class DataQuality(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNVERIFIABLE = "UNVERIFIABLE"


class ConfidenceSemantics(str, Enum):
    """Declared meaning of a probability in [0,1]. Never assume P(win)."""

    EVIDENCE_SUPPORT = "evidence_support"
    ANSWER_DISTRIBUTION_CONFIDENCE = "answer_distribution_confidence"
    QUESTION_YES_PROBABILITY = "question_yes_probability"
    SELF_REPORTED_ANSWER_CONFIDENCE = "self_reported_answer_confidence"
    CALIBRATED_EVENT_PROBABILITY = "calibrated_event_probability"
    UNSPECIFIED = "unspecified"


class Dataset(str, Enum):
    DAILY_BARS = "daily_bars"
    QUOTE = "quote"
    FUNDAMENTALS = "fundamentals"
    NEWS = "news"
    CALENDAR = "calendar"
    MACRO = "macro"
    MONEY_FLOW = "money_flow"


class ProviderKind(str, Enum):
    DEEPSEEK = "deepseek"
    JEV_HTTP = "jev_http"
    JEV_TYPESAFE = "jev_typesafe"
    STUB = "stub"
