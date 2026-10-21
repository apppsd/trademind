"""OMS: order lifecycle, idempotent submission, fill application (TASK-16).

States: NEW → SUBMITTING → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED, with
CANCEL_PENDING → CANCELLED / REJECTED / UNKNOWN. Crash with in-flight submits
marks UNKNOWN; recovery reconciles from the event log before new risk.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib

from ..domain.base import new_id, utcnow
from ..domain.enums import OrderSide, OrderStatus, RunMode, GuardVerdict, TradeAction
from ..storage.db import dumps
from ..domain.intents import GuardResult, PortfolioIntent
from ..domain.orders import Fill, Order
from ..errors import DuplicateKey, RecoveryRequired
from ..market.profiles import get_profile
from ..portfolio import Ledger
from ..portfolio.ledger import compute_fees
from ..storage import AuditSink, Repository
from .paper import PaperBroker


class OMS:
    def __init__(self, repo: Repository, ledger: Ledger, broker: PaperBroker,
                 audit: AuditSink, mode: str = "paper") -> None:
        self.repo = repo
        self.ledger = ledger
        self.broker = broker
        self.audit = audit
        self.mode = mode

    # -- submission ------------------------------------------------------------
    def submit(self, intent: PortfolioIntent, guard: GuardResult, qty: Decimal,
               limit_price: Decimal, now: datetime | None = None) -> Order:
        with self.ledger.atomic():
            return self._submit(intent, guard, qty, limit_price, now or utcnow())

    def _submit(self, intent, guard, qty, limit_price, now):
        """Idempotent per decision: same decision+version returns the same order."""
        if not isinstance(guard, GuardResult):
            raise RecoveryRequired("persisted Guard authorization required")
        rows = self.repo.db.query("SELECT payload_json FROM guard_results WHERE guard_id=?", (guard.id,))
        if not rows or GuardResult.model_validate_json(rows[0]["payload_json"]) != guard:
            raise RecoveryRequired("unknown or modified Guard authorization")
        if (guard.verdict != GuardVerdict.ALLOW or guard.intent_id != intent.intent_id
                or guard.details.get("intent_hash") != hashlib.sha256(dumps(intent).encode()).hexdigest()
                or Decimal(guard.details.get("qty", "0")) != qty
                or Decimal(guard.details.get("limit_price", "0")) != limit_price
                or intent.account_id != self.ledger.account_id or intent.mode.value != self.mode
                or self.mode not in ("paper", "replay")
                or intent.action not in (TradeAction.BUY, TradeAction.SELL)):
            raise RecoveryRequired("order differs from Guard authorization")
        client_order_id = f"cli-{intent.decision_id}-v{intent.portfolio_version}"
        existing = self.repo.get_order_by_client_id(client_order_id)
        if existing is not None:
            if existing["guard_id"] != guard.id:
                raise RecoveryRequired("decision already authorized differently")
            return Order.model_validate(existing)
        if now > guard.expires_at or now < guard.checked_at or guard.account_version != self.ledger.version:
            raise RecoveryRequired("expired or stale Guard authorization")
        own_open = [o for o in self.repo.open_orders() if o["account_id"] == self.ledger.account_id]
        if self.repo.get_kill_switch()["engaged"] or any(
                o["status"] in ("UNKNOWN", "SUBMITTING", "CANCEL_PENDING") for o in own_open):
            raise RecoveryRequired("execution blocked pending recovery or kill switch")
        if any(o['instrument_id'] == intent.instrument_id for o in own_open):
            raise RecoveryRequired("instrument already has an open order")
        if self.repo.db.query("SELECT 1 FROM orders WHERE json_extract(payload_json, '$.guard_id')=?", (guard.id,)):
            raise RecoveryRequired("Guard authorization already consumed")
        if intent.action == TradeAction.BUY and not any(
                r["order_id"] == guard.details["reservation_key"]
                and Decimal(r["amount"]) >= qty * limit_price for r in self.repo.active_reservations(intent.account_id)):
            raise RecoveryRequired("missing cash reservation")
        order = Order(
            id=new_id("o"), trace_id=intent.trace_id, created_at=now or utcnow(),
            order_id=new_id("o"), client_order_id=client_order_id,
            intent_id=intent.intent_id, guard_id=guard.id,
            account_id=intent.account_id, instrument_id=intent.instrument_id,
            mode=RunMode(self.mode),
            side=OrderSide.BUY if intent.action.value == "BUY" else OrderSide.SELL,
            quantity=str(qty), limit_price=str(limit_price), updated_at=utcnow(),
        )
        order.status = OrderStatus.SUBMITTING
        self.repo.save_order(order)
        order.status = OrderStatus.ACKNOWLEDGED
        self.repo.update_order(order)
        self.audit.record("oms", "submit", order.order_id,
                          {"client_order_id": client_order_id, "qty": str(qty)})
        return order

    def expire_approvals(self, now):
        """Release abandoned approvals after TTL; consumed approvals belong to OMS.
        Account-scoped (TASK-20261017-08): only approvals whose reservation
        belongs to this ledger are touched."""
        with self.ledger.atomic():
            my_keys = {r["order_id"] for r in self.repo.active_reservations(self.ledger.account_id)}
            rows = self.repo.db.query("SELECT g.payload_json FROM guard_results g WHERE g.verdict='ALLOW' "
                                      "AND NOT EXISTS (SELECT 1 FROM orders o WHERE json_extract(o.payload_json,'$.guard_id')=g.guard_id)")
            for row in rows:
                gr = GuardResult.model_validate_json(row['payload_json'])
                if gr.expires_at < now and gr.details.get('reservation_key') in my_keys:
                    self.ledger.release(gr.details['reservation_key'], now)

    def expire_stale_orders(self, now: datetime, no_new_entry_time: str | None = None) -> list[str]:
        """Intraday order lifecycle (ISSUE-20261016-06; semantics in ARCH-021).

        Frozen semantics: in the T0 intraday profiles a BUY is a day order —
        it expires at the trading-day change and is withdrawn once the
        no-new-entry cutoff passes, so a stale plan can never open new risk on
        a later day. SELL orders keep working: closing/reducing risk is never
        auto-cancelled. UNKNOWN/SUBMITTING stay untouched behind the recovery
        gate. Cancels release the frozen remainder atomically (via _cancel)
        and are idempotent. Guard approval TTL only bounds submission time;
        it is deliberately not reused as an order TTL.
        """
        from ..market.base import BEIJING
        expired = []
        with self.ledger.atomic():
            now_day = now.astimezone(BEIJING).date()
            cutoff = now.astimezone(BEIJING).strftime("%H:%M")
            for o in self.repo.open_orders():
                if o["account_id"] != self.ledger.account_id:
                    continue
                if o["status"] not in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                    continue
                order = Order.model_validate(o)
                created_day = order.created_at.astimezone(BEIJING).date()
                day_rollover = now_day > created_day
                eob_withdraw = (no_new_entry_time is not None
                                and order.side == OrderSide.BUY
                                and cutoff >= no_new_entry_time)
                if (day_rollover and order.side == OrderSide.BUY) or eob_withdraw:
                    self._cancel(order.order_id, at=now)
                    expired.append(order.order_id)
        return expired

    def cancel(self, order_id: str, at: datetime | None = None) -> Order | None:
        with self.ledger.atomic():
            return self._cancel(order_id, at or utcnow())

    def _cancel(self, order_id, at=None):
        at = at or utcnow()
        rows = self.repo.db.query("SELECT payload_json FROM orders WHERE order_id=?", (order_id,))
        if not rows:
            return None
        order = Order.model_validate_json(rows[0]["payload_json"])
        if order.account_id != self.ledger.account_id:
            # TASK-20261017-08: lifecycle operations are bound to this OMS's
            # account; foreign orders are rejected with zero side effects.
            raise RecoveryRequired(
                f"order {order_id} belongs to account {order.account_id!r}, not {self.ledger.account_id!r}")
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return order
        order.status = OrderStatus.CANCEL_PENDING
        self.repo.update_order(order)
        order.status = OrderStatus.CANCELLED
        self.repo.update_order(order)
        self.ledger.release(f"pending-{order.intent_id}", at)
        self.audit.record("oms", "cancel", order_id)
        return order

    # -- fills -----------------------------------------------------------------
    def poll_fill(self, order_id: str, now: datetime) -> Fill | None:
        with self.ledger.atomic():
            return self._poll_fill(order_id, now)

    def _poll_fill(self, order_id, now):
        rows = self.repo.db.query("SELECT payload_json FROM orders WHERE order_id=?", (order_id,))
        if not rows:
            return None
        order = Order.model_validate_json(rows[0]["payload_json"])
        if order.account_id != self.ledger.account_id:
            # TASK-20261017-08: never fill another account's order against this
            # ledger — reject loudly before any cash/position mutation.
            raise RecoveryRequired(
                f"order {order_id} belongs to account {order.account_id!r}, not {self.ledger.account_id!r}")
        if order.status not in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED):
            return None
        fill = self.broker.try_fill(order, now)
        if fill is None:
            return None
        if (fill.order_id != order.order_id or fill.quantity > order.quantity - order.filled_quantity
                or not order.created_at < fill.executed_at <= fill.available_at <= now
                or (order.side == OrderSide.BUY and fill.price > order.limit_price)
                or (order.side == OrderSide.SELL and fill.price < order.limit_price)):
            raise RecoveryRequired("invalid or future broker fill")
        # Fee authority is the ledger's compute_fees (Issue17-02): stamp the
        # deterministic expected fee onto the Fill BEFORE persisting, so the
        # returned Fill, the stored fill and the ledger event all carry one
        # number. apply_fill recomputes with identical inputs; the returned
        # value below is assigned back as the authoritative figure.
        fill.fee = compute_fees(order.side, fill.quantity, fill.price, self.ledger.fees)
        if not self.repo.save_fill(fill):  # duplicate provider_fill_id
            return None
        fee = self.ledger.apply_fill(fill, order, fill.executed_at)
        fill.fee = fee
        previous_notional = order.filled_quantity * (order.average_fill_price or Decimal("0"))
        order.filled_quantity += fill.quantity
        order.average_fill_price = (previous_notional + fill.quantity * fill.price) / order.filled_quantity
        order.status = self.broker.remaining_status(order)
        order.updated_at = fill.available_at
        self.repo.update_order(order)
        if order.status == OrderStatus.FILLED:
            self.ledger.release(f"pending-{order.intent_id}", now)
        elif order.side == OrderSide.BUY:
            key = f"pending-{order.intent_id}"
            remaining = order.quantity - order.filled_quantity
            self.ledger.resize_reservation(key, remaining * order.limit_price +
                                compute_fees(OrderSide.BUY, remaining, order.limit_price, self.ledger.fees), now)
        self.audit.record("oms", "fill", fill.fill_id,
                          {"order": order.order_id, "qty": str(fill.quantity),
                           "price": str(fill.price)})
        return fill

    # -- recovery ----------------------------------------------------------------
    def recover(self) -> dict:
        """After restart: UNKNOWN in-flight orders, reconcile before new risk."""
        open_orders = self.repo.open_orders()
        report = {"unknown": 0, "acknowledged": 0, "cancelled_pending": 0}
        for o in open_orders:
            if o["account_id"] != self.ledger.account_id:
                continue  # TASK-20261017-08: recovery never touches other accounts
            status = o["status"]
            if status == "SUBMITTING":
                obj = Order.model_validate(o)
                obj.status = OrderStatus.UNKNOWN
                self.repo.update_order(obj)
                report["unknown"] += 1
            elif status == "CANCEL_PENDING":
                report["cancelled_pending"] += 1
            else:
                report["acknowledged"] += 1
        return report

    def reconcile_unknown(self, order_id: str, resolved_status: str) -> Order:
        with self.ledger.atomic():
            return self._reconcile_unknown(order_id, resolved_status)

    def _reconcile_unknown(self, order_id, resolved_status):
        """Explicit reconciliation; never silently re-submits."""
        rows = self.repo.db.query("SELECT payload_json FROM orders WHERE order_id=?", (order_id,))
        if not rows:
            raise RecoveryRequired(f"order not found: {order_id}")
        order = Order.model_validate_json(rows[0]["payload_json"])
        if order.account_id != self.ledger.account_id:
            raise RecoveryRequired(
                f"order {order_id} belongs to account {order.account_id!r}, not {self.ledger.account_id!r}")
        if order.status != OrderStatus.UNKNOWN or resolved_status not in ("ACKNOWLEDGED", "CANCELLED", "REJECTED"):
            raise RecoveryRequired("unsupported reconciliation; fill evidence required for filled states")
        order.status = OrderStatus(resolved_status)
        self.repo.update_order(order)
        if order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self.ledger.release(f"pending-{order.intent_id}", utcnow())
        self.audit.record("oms", "reconcile", order_id, {"resolved": resolved_status})
        return order
