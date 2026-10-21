"""Hard Guard (TASK-15). Check order per ARCH-001:

mode/kill-switch → market legality → freshness (context/thesis/intent) →
portfolio version → action/weight consistency → equity/cash/open commitments →
concentration/exposure/daily loss → quantity/price legality → idempotency.
Only ALLOW / VETO / REPLAN; it never rewrites an intent or resizes positions.
Reservation is atomic with the approval (single lock + ledger reserve).
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Callable

from ..config import RiskConfig, T0ExperimentConfig
from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot
from ..domain.enums import GuardVerdict, RunMode, TradeAction
from ..domain.intents import GuardResult, PortfolioIntent
from ..domain.orders import Order
from ..errors import InsufficientFunds
from ..market.base import BEIJING
from ..market.profiles import get_profile
from ..portfolio import Ledger
from ..portfolio.ledger import compute_fees
from ..domain.enums import OrderSide
import hashlib
from ..storage.db import dumps
from ..storage import AuditSink, Repository

ZERO = Decimal("0")


class Guard:
    def __init__(self, repo: Repository, ledger: Ledger, risk: RiskConfig,
                 mode: str, universe: list[str], audit: AuditSink,
                 day_start_equity_provider: Callable[[], Decimal] | None = None,
                 valuation_complete_provider: Callable[[], bool] | None = None,
                 t0_config: T0ExperimentConfig | None = None) -> None:
        self.repo = repo
        self.ledger = ledger
        self.risk = risk
        self.t0_config = t0_config
        self.mode = mode
        self.universe = set(universe)
        self.audit = audit
        self.day_start_equity_provider = day_start_equity_provider or (lambda: ZERO)
        # False while any holding lacks a fresh legal price; new risk (BUY) is
        # refused until the whole account can be valued (ISSUE-20261013-05).
        self.valuation_complete_provider = valuation_complete_provider or (lambda: True)
        self._lock = threading.RLock()
        self._approved_decisions: set[tuple[str, int]] = set()
        self.consecutive_losses: int = 0
        self.daily_buy_orders_count: int = 0

    def record_trade_result(self, pnl: Decimal) -> None:
        with self._lock:
            if pnl < ZERO:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

    def reset_daily_counters(self) -> None:
        with self._lock:
            self.daily_buy_orders_count = 0

    def sync_counters(self, now: datetime) -> None:
        """Reconstruct completed round-trip loss streak from immutable fill events."""
        day = now.astimezone(BEIJING).date().isoformat()
        state = self.repo.get_coalesce('t0-approvals:' + self.ledger.account_id)
        self.daily_buy_orders_count = (state['meta']['count'] if state and state['meta'].get('day') == day else 0)
        existing_orders = self.repo.db.query("SELECT created_at FROM orders WHERE account_id=? AND side='BUY'", (self.ledger.account_id,))
        self.daily_buy_orders_count = max(self.daily_buy_orders_count, sum(
            datetime.fromisoformat(r['created_at']).astimezone(BEIJING).date().isoformat() == day for r in existing_orders))
        lots, streak = {}, 0
        for event in self.repo.account_events(self.ledger.account_id):
            if datetime.fromisoformat(event['effective_at']) > now:
                continue
            if event['kind'] not in ('fill_buy', 'fill_sell'):
                continue
            iid, qty = event['instrument_id'], Decimal(event['quantity'])
            amount = Decimal(event['amount'])
            lot = lots.setdefault(iid, [ZERO, ZERO, ZERO])  # qty, remaining cost, realized pnl
            if event['kind'] == 'fill_buy':
                lot[0] += qty
                lot[1] += amount
            else:
                basis = lot[1] * qty / lot[0]
                lot[2] += amount - basis
                lot[0] -= qty
                lot[1] -= basis
                if lot[0] == ZERO:
                    streak = streak + 1 if lot[2] < ZERO else 0
                    del lots[iid]
        self.consecutive_losses = streak

    # -- helpers -----------------------------------------------------------
    def _kill_switch(self) -> dict:
        return self.repo.get_kill_switch()

    def _open_commitments(self, snapshot) -> Decimal:
        return snapshot.reserved_cash

    def _round_lot(self, qty: Decimal, lot: int) -> Decimal:
        return (qty / lot).to_integral_value(rounding="ROUND_DOWN") * lot

    # -- main entry ----------------------------------------------------------
    def check(self, intent: PortfolioIntent, snap: ContextSnapshot,
              reference_price: Decimal, now: datetime | None = None) -> tuple[GuardResult, Decimal | None]:
        now = now or utcnow()
        reasons: list[str] = []
        details: dict[str, str] = {}

        with self._lock, self.ledger.atomic():  # approval, reservation and audit commit together
            if self.t0_config and self.t0_config.enabled:
                self.sync_counters(now)
            # 1. mode + kill switch
            if self.mode == RunMode.LIVE.value:
                return self._verdict(intent, ["mode_live_forbidden"], now, details), None
            if self.mode == RunMode.SHADOW.value:
                return self._verdict(intent, ["shadow_no_execution"], now, details), None
            ks = self._kill_switch()
            if ks["engaged"]:
                return self._verdict(intent, ["kill_switch"], now, {"reason": ks["reason"]}), None

            # New risk requires a complete account valuation; deterministic
            # exits (SELL) stay reachable through the rest of the checks.
            if intent.action == TradeAction.BUY and not self.valuation_complete_provider():
                return self._verdict(intent, ["incomplete_account_valuation"], now,
                                     {"detail": "a holding lacks a fresh legal price; no new risk"}), None

            # 9-first: one decision+version approves exactly once (the approval's
            # own reservation advances the account version, so this check must
            # precede the version comparison below)
            key = (intent.decision_id, intent.portfolio_version)
            persisted = self.repo.db.query("SELECT payload_json FROM guard_results WHERE verdict='ALLOW' AND intent_id=?", (intent.intent_id,))
            if key in self._approved_decisions or persisted:
                return self._verdict(intent, ["duplicate_decision"], now, details), None

            # 2. market legality
            if intent.instrument_id not in self.universe:
                return self._verdict(intent, ["instrument_out_of_universe"], now, details), None
            try:
                profile = get_profile(intent.instrument_id)
            except ValueError as exc:
                return self._verdict(intent, ["market_profile_unknown"], now, {"err": str(exc)}), None
            if intent.mode == RunMode.LIVE:
                return self._verdict(intent, ["live_mode_forbidden"], now, details), None
            if self.t0_config and self.t0_config.enabled and intent.action == TradeAction.BUY:
                if intent.instrument_id not in self.t0_config.whitelist or profile.t_plus_days != 0:
                    return self._verdict(intent, ["t0_whitelist_or_settlement"], now, details), None

            # 3. freshness
            if intent.valid_until < now:
                return self._verdict(intent, ["intent_expired"], now, details), None
            if snap.expires_at < now:
                return self._verdict(intent, ["context_expired"], now, details), None

            # 4. portfolio version
            if intent.account_id != self.ledger.account_id or intent.mode.value != self.mode:
                return self._verdict(intent, ["account_or_mode_mismatch"], now, details), None
            if reference_price <= ZERO or snap.as_of > now:
                return self._verdict(intent, ["invalid_reference_or_future_context"], now, details), None
            prices = {intent.instrument_id: reference_price}
            if snap.portfolio:
                for inst, p in snap.portfolio.positions.items():
                    if inst not in prices and Decimal(p.get("qty", "0")) > 0:
                        prices[inst] = Decimal(p["market_value"]) / Decimal(p["qty"])
            account = self.ledger.snapshot(now, prices)
            if any(p.instrument_id not in prices for p in account.positions):
                return self._verdict(intent, ["missing_position_valuation"], now, details), None
            if any(o["status"] in ("UNKNOWN", "SUBMITTING", "CANCEL_PENDING")
                   for o in self.repo.open_orders() if o["account_id"] == self.ledger.account_id):
                # TASK-20261017-08: account-scoped — foreign accounts' in-flight
                # orders never block this account's new risk.
                return self._verdict(intent, ["recovery_required"], now, details), None
            if any(o["instrument_id"] == intent.instrument_id for o in self.repo.open_orders()
                   if o["account_id"] == self.ledger.account_id):
                # TASK-20261017-08: foreign same-instrument orders do not block.
                return self._verdict(intent, ["pending_instrument_order"], now, details), None
            if snap.portfolio is None or intent.portfolio_version != snap.portfolio.portfolio_version:
                return self._verdict(intent, ["portfolio_version_mismatch"], now, details), None
            if account.version != intent.portfolio_version:
                return self._verdict(intent, ["account_version_advanced"], now,
                                     {"account_version": str(account.version)}), None

            # 5. action/weight consistency
            cur_w = account.position_weight(intent.instrument_id) or ZERO
            tgt_w = intent.target_weight
            if intent.action in (TradeAction.HOLD, TradeAction.WAIT):
                return self._verdict(intent, ["hold_no_trade"], now, details), None
            if intent.action == TradeAction.BUY and (tgt_w is None or tgt_w <= cur_w):
                return self._verdict(intent, ["buy_weight_not_above_current"], now, details), None
            if intent.action == TradeAction.SELL and (tgt_w is None or tgt_w < ZERO or tgt_w >= cur_w):
                return self._verdict(intent, ["sell_weight_negative"], now, details), None

            # 6/7. funds, concentration, exposure, daily loss
            equity = account.equity
            if equity <= 0:
                return self._verdict(intent, ["equity_nonpositive"], now, details), None
            pos_value = ZERO
            for p in account.positions:
                if p.instrument_id == intent.instrument_id:
                    pos_value = p.market_value or ZERO
            day_start = self.day_start_equity_provider()
            if day_start > 0:
                day_loss = (day_start - equity) / day_start
                max_daily_loss = self.t0_config.max_daily_loss_ratio if (self.t0_config and self.t0_config.enabled) else self.risk.max_daily_loss
                if day_loss >= max_daily_loss and intent.action == TradeAction.BUY:
                    return self._verdict(
                        intent, ["daily_loss_limit"], now,
                        {"day_loss": str(day_loss), "limit": str(self.risk.max_daily_loss)}), None
            open_comm = self._open_commitments(account)
            projected_weight = tgt_w if tgt_w is not None else cur_w
            max_pos_w = (self.t0_config.max_single_exposure / equity) if (self.t0_config and self.t0_config.enabled) else self.risk.max_position_weight
            if projected_weight > max_pos_w:
                return self._verdict(
                    intent, [("max_single_exposure_limit" if (self.t0_config and self.t0_config.enabled) else "position_weight_limit")], now,
                    {"target": str(projected_weight), "limit": str(max_pos_w)}), None
            total_exposure = sum(
                (p.market_value or ZERO) for p in account.positions) / equity
            if intent.action == TradeAction.BUY:
                projected_exposure = total_exposure + open_comm / equity + (projected_weight - cur_w)
                if projected_exposure > self.risk.max_total_exposure:
                    return self._verdict(
                        intent, ["total_exposure_limit"], now,
                        {"projected": str(projected_exposure),
                         "limit": str(self.risk.max_total_exposure)}), None
            if len(account.positions) >= self.risk.max_open_positions and intent.action == TradeAction.BUY \
                    and cur_w == 0:
                return self._verdict(intent, ["max_open_positions"], now, details), None

            if self.t0_config and self.t0_config.enabled and intent.action == TradeAction.BUY:
                if self.consecutive_losses >= self.t0_config.consecutive_losses_limit:
                    return self._verdict(
                        intent, ["consecutive_losses_limit"], now,
                        {"consecutive_losses": str(self.consecutive_losses), "limit": str(self.t0_config.consecutive_losses_limit)}), None
                if self.daily_buy_orders_count >= self.t0_config.daily_new_orders_limit:
                    return self._verdict(
                        intent, ["daily_new_orders_limit"], now,
                        {"daily_buy_orders": str(self.daily_buy_orders_count), "limit": str(self.t0_config.daily_new_orders_limit)}), None
                now_bj = now.astimezone(BEIJING)
                if now_bj.strftime("%H:%M") >= self.t0_config.no_new_entry_time:
                    return self._verdict(
                        intent, ["no_new_entry_time_reached"], now,
                        {"time": now_bj.strftime("%H:%M"), "cutoff": self.t0_config.no_new_entry_time}), None

            # 8. quantity/price legality (lot/tick rounding is an execution constraint)
            delta = (projected_weight * equity - pos_value) / reference_price
            qty = delta if intent.action == TradeAction.BUY else -delta
            position = next((p for p in account.positions if p.instrument_id == intent.instrument_id), None)
            qty = position.quantity if intent.action == TradeAction.SELL and tgt_w == ZERO and position else self._round_lot(qty, profile.lot_size)
            if intent.action == TradeAction.SELL and (position is None or qty > position.available_quantity):
                return self._verdict(intent, ["sell_exceeds_available"], now, details), None
            if qty <= 0:
                return self._verdict(intent, ["replan_zero_lot_quantity"], now, details), None
            execution_price = intent.limit_price or reference_price
            if execution_price <= ZERO or execution_price % profile.price_tick != ZERO:
                return self._verdict(intent, ["invalid_limit_price"], now, details), None
            notional = qty * execution_price
            if notional > self.risk.max_order_notional:
                return self._verdict(intent, ["order_notional_limit"], now, details), None
            fee = compute_fees(OrderSide.BUY if intent.action == TradeAction.BUY else OrderSide.SELL, qty, execution_price, self.ledger.fees)
            needed = notional + fee
            if intent.action == TradeAction.BUY:
                needed = notional + fee
                if self.t0_config and self.t0_config.enabled:
                    min_cash = self.t0_config.min_cash_reserve
                    if account.cash - open_comm - min_cash < needed:
                        return self._verdict(
                            intent, ["min_cash_reserve_violation"], now,
                            {"needed": str(needed), "available": str(account.cash - open_comm - min_cash), "reserve": str(min_cash)}), None
                    if pos_value + notional > self.t0_config.max_single_exposure:
                        return self._verdict(
                            intent, ["max_single_exposure_limit"], now,
                            {"projected": str(pos_value + notional), "limit": str(self.t0_config.max_single_exposure)}), None
                    cur_total_val = sum((p.market_value or ZERO) for p in account.positions)
                    if cur_total_val + open_comm + notional > self.t0_config.max_total_exposure:
                        return self._verdict(
                            intent, ["max_total_exposure_limit"], now,
                            {"projected": str(cur_total_val + open_comm + notional), "limit": str(self.t0_config.max_total_exposure)}), None
                elif needed > account.cash - open_comm:
                    return self._verdict(
                        intent, ["insufficient_free_cash"], now,
                        {"needed": str(needed), "free": str(account.cash - open_comm)}), None
            price_dev = abs(reference_price - (intent.limit_price or reference_price)) / \
                (intent.limit_price or reference_price)
            if intent.limit_price and price_dev > self.risk.price_tolerance:
                return self._verdict(
                    intent, ["price_moved_beyond_tolerance"], now,
                    {"ref": str(reference_price), "limit_price": str(intent.limit_price)}), None

            # atomic reservation with approval
            if intent.action == TradeAction.BUY:
                try:
                    self.ledger.reserve(order_id=f"pending-{intent.intent_id}",
                                        amount=needed,
                                        at=now)
                except InsufficientFunds as exc:
                    return self._verdict(intent, ["reserve_failed"], now, {"err": str(exc)}), None
            if intent.action == TradeAction.BUY:
                self.daily_buy_orders_count += 1
                if self.t0_config and self.t0_config.enabled:
                    self.repo.save_coalesce('t0-approvals:' + self.ledger.account_id, now,
                                           {'day': now.astimezone(BEIJING).date().isoformat(),
                                            'count': self.daily_buy_orders_count})
            self.audit.record_critical("guard", "approve", intent.intent_id,
                                       {"decision": intent.decision_id, "qty": str(qty)})
            result = GuardResult(
                id=new_id("gr"), trace_id=intent.trace_id, created_at=utcnow(),
                intent_id=intent.intent_id, account_version=self.ledger.version,
                checked_at=now, verdict=GuardVerdict.ALLOW, reason_codes=[],
                policy_version=self.risk.policy_version,
                expires_at=min(intent.valid_until, snap.expires_at), details={
                    "qty": str(qty), "limit_price": str(execution_price),
                    "intent_hash": hashlib.sha256(dumps(intent).encode()).hexdigest(),
                    "reservation_key": f"pending-{intent.intent_id}"},
            )
            self.repo.save_guard_result(result.id, result)
            return result, qty

    def _verdict(self, intent: PortfolioIntent, reasons: list[str],
                 now: datetime, details: dict[str, str]) -> tuple[GuardResult, None]:
        result = GuardResult(
            id=new_id("gr"), trace_id=intent.trace_id, created_at=utcnow(),
            intent_id=intent.intent_id, account_version=self.ledger.version,
            checked_at=now, verdict=GuardVerdict.VETO, reason_codes=reasons,
            policy_version=self.risk.policy_version,
            expires_at=intent.valid_until, details=details,
        )
        self.repo.save_guard_result(result.id, result)
        self.audit.record("guard", "veto", intent.intent_id, {"reasons": reasons})
        return result


def check_stop_loss_exit(
    position_avg_cost: Decimal,
    current_price: Decimal,
    stop_loss_ratio: Decimal = Decimal("0.008"),
) -> bool:
    """Returns True if position loss meets or exceeds stop_loss_ratio (0.8%)."""
    if position_avg_cost <= ZERO:
        return False
    loss = (position_avg_cost - current_price) / position_avg_cost
    return loss >= stop_loss_ratio


def check_session_close_exit(
    now: datetime,
    close_out_time: str = "14:55",
) -> bool:
    """Returns True if current time is on or after session close-out time."""
    now_bj = now.astimezone(BEIJING)
    return now_bj.strftime("%H:%M") >= close_out_time
