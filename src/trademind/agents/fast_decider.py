"""JEV narrow independent questions become a bounded action, not order authority."""
from datetime import datetime, timedelta
from decimal import Decimal
from ..domain.strategy import DecisionOutcome
from ..domain.base import new_id
from ..domain.enums import TradeAction
from ..models.jev import probability
from ..errors import ProviderError
from .fast_prompts import FAST_PROMPT_VERSION, fast_questions

class FastDecider:
    def __init__(self,jev,cfg,clock):
        self.jev,self.cfg,self.clock=jev,cfg,clock

    def decide(self,state,trace_id):
        now=self.clock()
        result=DecisionOutcome(decision_id=new_id('fast'),raw_action='WAIT',action=TradeAction.WAIT,
            reason='no valid answer',plan_id=state.plan.plan_id,context_id=state.context_id,
            account_version=state.account['version'],as_of=state.data_cutoff,
            prompt_version=FAST_PROMPT_VERSION,
            valid_until=min(state.plan.valid_until,state.data_cutoff+timedelta(seconds=180),
                            datetime.fromisoformat(state.bars[-1]['time'])+timedelta(seconds=180)),answers={},model=self.jev.model)
        if not state.plan.valid_at(now,state.instrument_id,state.rule_version) or now>=result.valid_until:
            result.reason='expired plan or context'
            return result
        if len(state.allowed_actions)==1:
            result.reason='no actionable strategy conditions'
            return result
        questions=fast_questions(state.allowed_actions)
        try:
            body=self.jev.evaluate(state=state.model_dump(mode='json'),questions=questions,trace_id=trace_id,
                                   question_version=FAST_PROMPT_VERSION)
            answers=body['answers']
            result.answers=answers
            result.raw_action=answers['action']['choice']
            result.action_confidence=probability(answers['action']['confidence'])
            result.model=body['model']
            result.usage=body['usage']
            result.latency_ms=self.jev.last_call.latency_ms
            result.cost=self.jev.last_call.cost_amount
            if result.action_confidence<self.cfg.action_confidence_floor or any(
                probability(answers[k]['noul'])<self.cfg.evidence_yes_floor for k in ('evidence_sufficient','plan_applicable')):
                result.reason='uncertain JEV evidence or plan'
                result.refresh_requested=True
                return result
            action=TradeAction(result.raw_action)
            if action not in state.allowed_actions:
                raise ProviderError('JEV action not allowed')
            if action==TradeAction.BUY and probability(answers['entry_suitable']['noul'])<self.cfg.evidence_yes_floor:
                result.reason='entry evidence insufficient'
                result.refresh_requested=True
                return result
            if not state.plan.valid_at(self.clock(),state.instrument_id,state.rule_version) or self.clock()>=result.valid_until:
                result.reason='expired during JEV request'
                return result
            result.action=action
            result.reason='typed JEV action with valid strategy and evidence'
        except (ProviderError,ValueError,KeyError,TypeError):
            result.reason='JEV provider or answer validation failed'
        return result
