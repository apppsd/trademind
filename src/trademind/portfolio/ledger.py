"""Account ledger: single-writer, Decimal, event-sourced, T+1 (TASK-17).

Facts only — no trading judgment. All amounts are Decimal; the ledger is the
only writer of account state; recovery replays account_events deterministically.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from contextlib import contextmanager
from datetime import timezone
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..domain.base import new_id, utcnow
from ..domain.enums import OrderSide
from ..domain.instruments import FeeSchedule
from ..domain.orders import AccountEvent, AccountSnapshot, Fill, FundReservation, Order, PositionState
from ..errors import DuplicateKey, InsufficientFunds
from ..storage import Database, Repository
from ..market.profiles import get_profile


def compute_fees(side: OrderSide, quantity: Decimal, price: Decimal, fees: FeeSchedule) -> Decimal:
    notional = quantity * price
    commission = max(notional * fees.commission_rate, fees.commission_min)
    total = commission
    if side == OrderSide.SELL:
        total += notional * fees.stamp_duty_rate_sell
    total += notional * fees.transfer_fee_rate
    return total.quantize(Decimal("0.01"))


@dataclass
class _Lot:
    quantity: Decimal = Decimal("0")
    available: Decimal = Decimal("0")  # T+1: bought-today shares unavailable
    avg_cost: Decimal = Decimal("0")
    bought_today: Decimal = Decimal("0")
    currency: str = "CNY"


def _parse_note(note: str) -> tuple[Decimal, Decimal]:
    fee = price = Decimal("0")
    for part in note.split(";"):
        if part.startswith("fee="):
            fee = Decimal(part[4:])
        elif part.startswith("price="):
            price = Decimal(part[6:])
    return fee, price


class Ledger:
    """Single-writer account ledger. `version` increments on every applied event."""

    def __init__(self, db: Database, account_id: str, fees: FeeSchedule) -> None:
        self.db = db
        self.repo = Repository(db)
        self.account_id = account_id
        self.fees = fees
        self._lock = threading.RLock()
        self._cash = Decimal("0")
        self._reserved = Decimal("0")
        self._positions: dict[str, _Lot] = {}
        self._version = 0
        self._seq = 0
        self._trading_day = None
        self._load()
        if self._seq and self.repo.load_account_state(self.account_id) is None:
            self.rebuild_from_events()

    # -- persistence -----------------------------------------------------------
    def _load(self) -> None:
        state = self.repo.load_account_state(self.account_id)
        if state:
            self._version = int(state["version"])
            self._cash = Decimal(state["cash"])
            self._reserved = Decimal(state.get("reserved_cash", "0"))
            for p in state.get("positions", []):
                self._positions[p["instrument_id"]] = _Lot(
                    Decimal(p["quantity"]), Decimal(p["available_quantity"]),
                    Decimal(p["avg_cost"]), Decimal(p["quantity"]) - Decimal(p["available_quantity"]), currency=p.get("currency", "CNY"))
        for ev in self.repo.account_events(self.account_id):
            self._seq = max(self._seq, int(ev["seq"]))
            if ev["kind"] == "rollover":
                self._trading_day = date.fromisoformat(ev["note"])

    @contextmanager
    def atomic(self):
        with self._lock:
            saved = deepcopy((self._cash, self._reserved, self._positions, self._version, self._seq, self._trading_day))
            try:
                with self.db.transaction():
                    state = self.repo.load_account_state(self.account_id)
                    if state and int(state['version']) != self._version:
                        self._positions = {}
                        self._load()
                    yield
            except BaseException:
                self._cash, self._reserved, self._positions, self._version, self._seq, self._trading_day = saved
                raise

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, kind: str, *, instrument_id: str | None = None,
              quantity: Decimal | None = None, amount: Decimal | None = None,
              fill_id: str | None = None, order_id: str | None = None,
              effective_at: datetime | None = None, note: str = "") -> AccountEvent:
        ev = AccountEvent(
            id=new_id("ae"), trace_id=new_id("tr"), created_at=utcnow(),
            seq=self._next_seq(), account_id=self.account_id, kind=kind,  # type: ignore[arg-type]
            instrument_id=instrument_id,
            quantity=str(quantity) if quantity is not None else None,
            amount=str(amount) if amount is not None else None,
            fill_id=fill_id, order_id=order_id,
            effective_at=effective_at or utcnow(), note=note,
        )
        if not self.repo.append_account_event(ev):
            raise DuplicateKey(f"account event fill already applied: {fill_id}")
        return ev

    def _persist(self, at: datetime) -> None:
        self._version += 1
        self.repo.save_account_state(self.snapshot(at))

    # -- recovery ---------------------------------------------------------------
    def rebuild_from_events(self) -> None:
        """Deterministic replay from the event log; ends at the same state."""
        with self.atomic():
            self._cash = Decimal("0")
            self._reserved = Decimal("0")
            self._positions = {}
            self._seq = 0
            for ev in self.repo.account_events(self.account_id):
                self._seq = int(ev["seq"])
                kind = ev["kind"]
                amount = Decimal(ev["amount"]) if ev.get("amount") else None
                qty = Decimal(ev["quantity"]) if ev.get("quantity") else None
                inst = ev["instrument_id"]
                fee, price = _parse_note(ev.get("note", ""))
                if kind == "deposit":
                    self._cash += amount
                elif kind == "withdraw":
                    self._cash -= amount
                elif kind == "reserve":
                    self._reserved += amount
                elif kind == "release":
                    self._reserved -= amount
                elif kind == "fill_buy":
                    lot = self._positions.setdefault(inst, _Lot())
                    notional = qty * price
                    self._cash -= notional + fee
                    total_cost = lot.avg_cost * lot.quantity + notional + fee
                    t0 = False
                    try:
                        from ..market.profiles import get_multi_asset_profile
                        t0 = get_multi_asset_profile(inst).t_plus_days == 0
                    except Exception:
                        try:
                            t0 = get_profile(inst).t_plus_days == 0
                        except Exception:
                            t0 = False
                    if t0:
                        # T+0 unlocks immediately and must NOT also count into
                        # bought_today: the rollover would unlock it a second
                        # time (available > quantity, ISSUE-20261016-03).
                        lot.available += qty
                    else:
                        lot.bought_today += qty
                    lot.quantity += qty
                    lot.avg_cost = (total_cost / lot.quantity).quantize(Decimal("0.0001"))
                elif kind == "rollover":
                    self._trading_day = date.fromisoformat(ev["note"])
                    if amount:
                        for lot in self._positions.values():
                            lot.available += lot.bought_today
                            lot.bought_today = Decimal("0")
                elif kind == "fill_sell":
                    lot = self._positions[inst]
                    self._cash += qty * price - fee
                    lot.quantity -= qty
                    lot.available -= qty
                    if lot.quantity == 0:
                        lot.avg_cost = Decimal("0")
                        del self._positions[inst]
            self._version = self._seq
            events = self.repo.account_events(self.account_id)
            if events:
                self.repo.save_account_state(self.snapshot(datetime.fromisoformat(events[-1]['effective_at'])))

    # -- funding -----------------------------------------------------------------
    def deposit(self, amount: Decimal, at: datetime | None = None) -> None:
        with self.atomic():
            self._emit("deposit", amount=amount, effective_at=at)
            self._cash += amount
            self._persist(at or utcnow())

    def withdraw(self, amount: Decimal, at: datetime | None = None) -> None:
        with self.atomic():
            if amount > self._cash - self._reserved:
                raise InsufficientFunds("withdraw exceeds free cash")
            self._emit("withdraw", amount=amount, effective_at=at)
            self._cash -= amount
            self._persist(at or utcnow())

    def reserve(self, order_id: str, amount: Decimal, at: datetime) -> str:
        """Atomic reservation tied to one order; called by Guard before submit."""
        with self.atomic():
            free = self._cash - self._reserved
            if amount > free:
                raise InsufficientFunds(f"need {amount}, free {free}")
            self._reserved += amount
            res_id = new_id("res")
            res = FundReservation(
                id=new_id("fr"), trace_id=new_id("tr"), created_at=utcnow(),
                reservation_id=res_id, order_id=order_id, account_id=self.account_id,
                account_version=self._version, amount=amount,
            )
            self.repo.save_reservation(res)
            self._emit("reserve", amount=amount, order_id=order_id, effective_at=at)
            self._persist(at)
            return res_id

    def release(self, order_id: str, at: datetime) -> None:
        with self.atomic():
            released = Decimal("0")
            for res in self.repo.active_reservations(self.account_id):
                if res["order_id"] == order_id:
                    released += Decimal(res["amount"])
                    self.repo.set_reservation_active(res["reservation_id"], False)
            if released == 0:
                return
            self._reserved -= released
            self._emit("release", amount=released, order_id=order_id, effective_at=at)
            self._persist(at)

    # -- fills --------------------------------------------------------------------
    def resize_reservation(self, order_id: str, amount: Decimal, at: datetime) -> None:
        with self.atomic():
            for res in self.repo.active_reservations(self.account_id):
                if res['order_id'] != order_id:
                    continue
                delta = Decimal(res['amount']) - amount
                if amount < 0 or -delta > self._cash - self._reserved:
                    raise InsufficientFunds('invalid remaining reservation')
                res['amount'] = str(amount)
                res['active'] = amount > 0
                from ..storage.db import dumps
                self.db.execute('UPDATE reservations SET amount=?, active=?, payload_json=? WHERE reservation_id=?',
                                (str(amount), int(amount > 0), dumps(res), res['reservation_id']))
                self._reserved -= delta
                self._emit('release', amount=delta, order_id=order_id, effective_at=at)
                self._persist(at)
                return
            raise InsufficientFunds('remaining reservation missing')

    def apply_fill(self, fill: Fill, order: Order, executed_at: datetime) -> Decimal:
        """Apply one fill (idempotent by fill_id). Returns fee charged.

        The event is persisted before any in-memory mutation: a redelivered
        fill_id raises DuplicateKey and leaves state untouched."""
        if order.account_id != self.account_id:
            # TASK-20261017-08 defense: a ledger never applies fills for
            # another account's order, whatever the caller passes in.
            raise ValueError(
                f"fill order account {order.account_id!r} does not match ledger {self.account_id!r}")
        with self.atomic():
            self.on_new_trading_day(executed_at.astimezone(timezone(__import__('datetime').timedelta(hours=8))).date())
            fee = compute_fees(order.side, fill.quantity, fill.price, self.fees)
            notional = fill.quantity * fill.price
            if order.side == OrderSide.BUY:
                if notional + fee > self._cash:
                    raise InsufficientFunds("buy fill exceeds cash")
                self._emit("fill_buy", instrument_id=order.instrument_id,
                           quantity=fill.quantity, amount=notional + fee,
                           fill_id=fill.fill_id, order_id=order.order_id,
                           effective_at=executed_at, note=f"fee={fee};price={fill.price}")
            else:
                lot = self._positions.get(order.instrument_id, _Lot())
                if fill.quantity > lot.available:
                    raise InsufficientFunds(
                        f"sell {fill.quantity} exceeds available {lot.available} (T+1)")
                self._emit("fill_sell", instrument_id=order.instrument_id,
                           quantity=fill.quantity, amount=notional - fee,
                           fill_id=fill.fill_id, order_id=order.order_id,
                           effective_at=executed_at, note=f"fee={fee};price={fill.price}")
            # event durably accepted → now mutate memory
            lot = self._positions.setdefault(order.instrument_id, _Lot())
            if order.side == OrderSide.BUY:
                self._cash -= notional + fee
                if self._cash < Decimal("0"):
                    raise InsufficientFunds("cash went negative on buy fill")
                total_cost = lot.avg_cost * lot.quantity + notional + fee  # buy fee joins basis
                t0 = False
                try:
                    from ..market.profiles import get_multi_asset_profile
                    t0 = get_multi_asset_profile(order.instrument_id).t_plus_days == 0
                except Exception:
                    try:
                        t0 = get_profile(order.instrument_id).t_plus_days == 0
                    except Exception:
                        t0 = False
                if t0:
                    # T+0 unlocks immediately and must NOT also count into
                    # bought_today: the rollover would unlock it a second time
                    # (available > quantity → ValidationError, ISSUE-20261016-03).
                    # _load recomputes bought_today as quantity-available, so all
                    # three recovery paths now agree it stays 0 for T+0 lots.
                    lot.available += fill.quantity
                else:
                    lot.bought_today += fill.quantity  # T+1: locked until rollover
                lot.quantity += fill.quantity
                lot.avg_cost = (total_cost / lot.quantity).quantize(Decimal("0.0001"))
            else:
                self._cash += notional - fee
                lot.quantity -= fill.quantity
                lot.available -= fill.quantity
                if lot.quantity == 0:
                    lot.avg_cost = Decimal("0")
                    del self._positions[order.instrument_id]
            self._persist(executed_at)
            return fee

    # -- valuation -----------------------------------------------------------------
    def on_new_trading_day(self, d: date) -> None:
        """T+1 rollover: shares bought earlier become sellable."""
        from ..market.calendar import TradingCalendar
        from ..errors import CalendarCoverageError
        try:
            confirmed = TradingCalendar.load().is_trading_day(d)
        except CalendarCoverageError:
            # Unknown coverage is never treated as a confirmed trading day:
            # shares stay locked (conservative) until the calendar is updated.
            return
        if not confirmed:
            return
        with self.atomic():
            if self._trading_day is not None and d <= self._trading_day:
                return
            # Never unlock shares acquired on this same Shanghai calendar day.
            last_buy = max((datetime.fromisoformat(e["effective_at"]).astimezone(timezone(__import__('datetime').timedelta(hours=8))).date()
                            for e in self.repo.account_events(self.account_id) if e["kind"] == "fill_buy"), default=None)
            unlock = last_buy is None or d > last_buy
            self._emit("rollover", amount=Decimal(int(unlock)), effective_at=datetime.combine(d, datetime.min.time(), timezone.utc), note=d.isoformat())
            self._trading_day = d
            if unlock:
                for lot in self._positions.values():
                    lot.available += lot.bought_today
                    lot.bought_today = Decimal("0")
            self._persist(datetime.combine(d, datetime.min.time(), timezone.utc))

    def snapshot(self, at: datetime, prices: dict[str, Decimal] | None = None) -> AccountSnapshot:
        with self._lock:
            return self._snapshot(at, prices)

    def _snapshot(self, at: datetime, prices: dict[str, Decimal] | None = None) -> AccountSnapshot:
        prices = prices or {}
        positions: list[PositionState] = []
        market_value = Decimal("0")
        for inst, lot in sorted(self._positions.items()):
            if lot.quantity == 0:
                continue
            last = prices.get(inst)
            mv = last * lot.quantity if last is not None else lot.avg_cost * lot.quantity
            market_value += mv
            positions.append(PositionState(
                id=new_id("ps"), trace_id=new_id("tr"), created_at=utcnow(),
                instrument_id=inst, quantity=lot.quantity,
                available_quantity=lot.available, avg_cost=lot.avg_cost,
                currency=lot.currency, last_price=last, market_value=mv,
                unrealized_pnl=(mv - lot.avg_cost * lot.quantity) if last is not None else None,
            ))
        return AccountSnapshot(
            id=new_id("as"), trace_id=new_id("tr"), created_at=utcnow(),
            account_id=self.account_id, version=self._version, as_of=at,
            cash=self._cash, reserved_cash=self._reserved,
            market_value=market_value, equity=self._cash + market_value,
            positions=positions,
        )

    def equity(self, prices: dict[str, Decimal] | None = None) -> Decimal:
        return self.snapshot(utcnow(), prices).equity

    @property
    def version(self) -> int:
        return self._version
