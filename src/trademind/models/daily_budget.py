"""Persistent shared request count and conservative DeepSeek cost reservations."""
from decimal import Decimal
from threading import Event

from ..domain.base import utcnow
from ..errors import ProviderError
from ..market.base import BEIJING


class ReservedCost(Decimal):
    """Decimal-compatible cost tagged with the day that owns its reservation."""
    def __new__(cls, amount, day):
        value = super().__new__(cls, str(amount))
        value.day = day
        return value


class DailyModelBudget:
    def __init__(self, repo, cfg, account_id, clock=utcnow):
        self.repo, self.cfg, self.clock = repo, cfg, clock
        self.key = 'model-budget:' + account_id
        self.limit = cfg.daily_decision_call_budget
        self._closing = Event()

    def close_requests(self):
        """Stop new reservations in this process; never alter persisted usage."""
        self._closing.set()

    def _state(self):
        day = self.clock().astimezone(BEIJING).date().isoformat()
        row = self.repo.get_coalesce(self.key)
        state = row['meta'] if row and row['meta'].get('day') == day else {
            'day': day, 'used': 0, 'cost_reserved_cny': '0'}
        state.setdefault('cost_settled_cny', '0')
        state.setdefault('cost_pending_cny', str(max(Decimal('0'),Decimal(state['cost_reserved_cny'])-Decimal(state['cost_settled_cny']))))
        return state

    @property
    def used(self):
        return self._state()['used']

    @property
    def exhausted(self):
        return self.used >= self.limit

    def try_consume(self):
        # Only explicit stubs use count-only reservations; real providers price before transport.
        return bool(self._reserve(Decimal('0')))

    def reserve_request(self, input_bound, output_bound, role="strategy"):
        rates = (self.cfg.model_input_cny_per_million,
                 self.cfg.model_output_cny_per_million)
        if any(v is None or Decimal(str(v)) <= 0 for v in rates):
            raise ProviderError('T0 paid model pricing unconfigured; request blocked')
        cost = (Decimal(input_bound) * Decimal(str(rates[0])) +
                Decimal(output_bound) * Decimal(str(rates[1]))) / Decimal('1000000')
        category = ('probe' if 'probe' in role else 'strategy') if self.cfg.profile == "jev_fast_decision" else None
        limit = self.cfg.probe_request_limit if category == 'probe' else self.cfg.strategy_request_limit
        day = self._reserve(cost, category, limit)
        if not day:
            raise ProviderError('T0 daily model count/cost budget exhausted')
        return ReservedCost(cost, day)

    def jev_cost(self, inputs, outputs):
        rates = (self.cfg.jev_input_cny_per_million, self.cfg.jev_output_cny_per_million)
        if (rates[0] is None or rates[1] is None or not all(Decimal(str(v)).is_finite() for v in rates)
            or Decimal(str(rates[0])) <= 0 or Decimal(str(rates[1])) < 0):
            raise ProviderError('JEV pricing unconfigured; paid request blocked')
        return (Decimal(inputs)*Decimal(str(rates[0]))+Decimal(outputs)*Decimal(str(rates[1])))/Decimal('1000000')

    def reserve_jev(self, input_bound, role='jev'):
        cost = self.jev_cost(input_bound, self.cfg.jev_output_token_reservation)
        category = 'probe' if role == 'jev_probe' else 'jev'
        limit = self.cfg.probe_request_limit if category == 'probe' else self.cfg.jev_request_limit
        day = self._reserve(cost, category, limit)
        if not day:
            raise ProviderError('JEV daily model count/cost budget exhausted')
        return ReservedCost(cost, day)

    def reconcile_jev(self, reserved, actual):
        self.reconcile(reserved, actual)

    def flash_cost(self, inputs, outputs):
        return (Decimal(inputs)*self.cfg.model_input_cny_per_million +
                Decimal(outputs)*self.cfg.model_output_cny_per_million)/Decimal('1000000')

    def reconcile(self, reserved, actual):
        # Never release reservations on transport failures. Reconcile known usage only.
        with self.repo.db.transaction():
            day = getattr(reserved, 'day', self.clock().astimezone(BEIJING).date().isoformat())
            current = self.repo.get_coalesce(self.key)
            key = self.key if current and current['meta'].get('day') == day else self.key + ':history:' + day
            row = self.repo.get_coalesce(key)
            if not row or row['meta'].get('day') != day:
                raise ProviderError('reservation day unavailable; settlement blocked')
            state = dict(row['meta'])
            state['cost_reserved_cny'] = str(max(Decimal('0'), Decimal(state['cost_reserved_cny'])-reserved+actual))
            state['cost_settled_cny'] = str(Decimal(state.get('cost_settled_cny','0'))+actual)
            state['cost_pending_cny'] = str(max(Decimal('0'),Decimal(state.get('cost_pending_cny','0'))-reserved))
            self.repo.save_coalesce(key, self.clock(), state)
        if actual > reserved:
            raise ProviderError('model billed usage exceeded reservation; decision blocked')

    def _reserve(self, cost, category=None, category_limit=None):
        with self.repo.db.transaction():
            if self._closing.is_set():
                raise ProviderError('model run is closing; new request blocked')
            state = self._state()
            total = Decimal(state['cost_reserved_cny']) + cost
            if state['used'] >= self.limit or total > self.cfg.daily_model_cost_limit_cny:
                return False
            if category and state.get(category, 0) >= category_limit:
                return False
            if category:
                state[category] = state.get(category, 0) + 1
            state['used'] += 1
            state['cost_reserved_cny'] = str(total)
            state['cost_pending_cny'] = str(Decimal(state.get('cost_pending_cny','0'))+cost)
            previous = self.repo.get_coalesce(self.key)
            if previous and previous['meta'].get('day') != state['day']:
                self.repo.save_coalesce(self.key + ':history:' + previous['meta']['day'],
                                        self.clock(), previous['meta'])
            self.repo.save_coalesce(self.key, self.clock(), state)
        return state['day']

    def snapshot(self):
        return dict(self._state())

    def check_probe_pair(self, flash_input_bound, jev_input_bound):
        """Read-only preflight for two single-attempt probes; no budget reset.

        Actual providers still reserve before transport. The paper runner owns
        the account's sole writer and calls this before starting a model worker.
        """
        rates = (self.cfg.model_input_cny_per_million, self.cfg.model_output_cny_per_million)
        if any(v is None or not Decimal(str(v)).is_finite() or Decimal(str(v)) <= 0 for v in rates):
            raise ProviderError('flash pricing unconfigured')
        cost = self.flash_cost(flash_input_bound, self.cfg.model_max_output_tokens)
        cost += self.jev_cost(jev_input_bound, self.cfg.jev_output_token_reservation)
        state = self._state()
        if state['used'] + 2 > self.limit or state.get('probe', 0) + 2 > self.cfg.probe_request_limit:
            raise ProviderError('two probe requests unavailable')
        if Decimal(state['cost_reserved_cny']) + cost > self.cfg.daily_model_cost_limit_cny:
            raise ProviderError('two probe cost reservations unavailable')
        return cost
