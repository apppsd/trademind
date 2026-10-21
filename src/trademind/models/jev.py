"""Official TypeSafe system-one adapter; typed answers, bounded calls, no secrets."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import requests

from ..domain.base import new_id, utcnow
from ..domain.enums import ConfidenceSemantics, ProviderKind
from ..domain.judgments import Judgment, JudgmentBatch
from ..errors import ProviderError, ProviderTimeout
from .base import Budget, CircuitBreaker, JEVProvider, build_call


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ProviderError("invalid probability")
    try:
        p = Decimal(str(value))
    except Exception:
        raise ProviderError("invalid probability") from None
    if not p.is_finite() or not 0 <= p <= 1:
        raise ProviderError("invalid probability")
    return p


def validate_answer(spec, answer):
    """Reject malformed official answers, never manufacture distributions."""
    kind = spec['type']
    if not isinstance(answer, dict) or answer.get('type') != kind:
        raise ProviderError('JEV answer type mismatch')
    if kind == 'noul':
        probability(answer.get('noul'))
        return
    probs = answer.get('probabilities')
    keys = set(spec['criteria']) if kind == 'choice' else {str(i) for i in range(len(spec['criteria']))}
    if not isinstance(probs, dict) or set(probs) != keys:
        raise ProviderError('JEV probability keys mismatch')
    values = {k: probability(v) for k, v in probs.items()}
    if abs(sum(values.values()) - 1) > Decimal('0.0001'):
        raise ProviderError('JEV probabilities do not sum to one')
    probability(answer.get('confidence'))
    if kind == 'choice':
        choice = answer.get('choice')
        if choice not in keys or values[choice] < max(values.values()):
            raise ProviderError('JEV invalid selected choice')
    else:
        legend = answer.get('legend')
        if legend != {str(i): v for i, v in enumerate(spec['criteria'])}:
            raise ProviderError('JEV score legend mismatch')
        try:
            score = Decimal(str(answer['score']))
        except Exception:
            raise ProviderError('JEV invalid score') from None
        expected = sum(Decimal(k)*v for k, v in values.items())
        if not score.is_finite() or abs(score-expected) > Decimal('0.0001'):
            raise ProviderError('JEV invalid weighted score')


class TypeSafeJEVProvider(JEVProvider):
    kind = ProviderKind.JEV_TYPESAFE

    def __init__(self, base_url='https://api.typesafe.ai', api_key='', model='jev-latest',
                 question_version='qv-fast-1', timeout_s=10.0, auditor=None, budget=None):
        if not base_url:
            raise ProviderError('TypeSafe JEV base_url missing')
        self.base_url, self.api_key = base_url.rstrip('/'), api_key
        self.model, self.question_version = model or 'jev-latest', question_version
        self.timeout_s, self.auditor = timeout_s, auditor
        self.budget = budget or Budget(limit=10**9)
        self.breaker, self.capability_verified = CircuitBreaker(), False
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        if api_key:
            self.session.headers['Authorization'] = 'Bearer ' + api_key

    @staticmethod
    def question_map(questions):
        result = {}
        for q in questions:
            qid = q.get('question_id') or q.get('id')
            if not isinstance(qid, str) or not qid or qid in result:
                raise ProviderError('missing/duplicate question id')
            kind = q.get('type', 'choice')
            if kind not in ('choice', 'noul', 'score'):
                raise ProviderError('unsupported question type')
            spec = {'type': kind, 'instructions': q.get('instructions', q.get('prompt', qid))}
            if not spec['instructions']:
                raise ProviderError('missing instructions')
            if kind != 'noul':
                criteria = q.get('criteria')
                if criteria is None:
                    options = q.get('options', ['yes', 'no', 'unsure'])
                    criteria = {o: o for o in options} if kind == 'choice' else options
                if not isinstance(criteria, dict if kind == 'choice' else list) or not 2 <= len(criteria) <= 10:
                    raise ProviderError('invalid criteria')
                spec['criteria'] = criteria
            result[qid] = spec
        if not result or len(result) > 10:
            raise ProviderError('bounded JEV question count exceeded')
        return result

    def evaluate(self, *, state, questions, trace_id, require_all=True, role='jev', question_version=None):
        self.breaker.check()
        if not state:
            raise ProviderError('JEV requires actual visible state')
        specs = self.question_map(questions)
        payload = {'model': self.model, 'state': state, 'questions': specs}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > 64000:
            raise ProviderError('JEV input exceeds bounded payload budget')
        call = build_call(role=role, provider=self.kind, model=self.model,
                          system=question_version or self.question_version, user=encoded, trace_id=trace_id)
        call.attempts = 0
        started = time.monotonic()
        try:
            if hasattr(self.budget, 'reserve_jev'):
                try:
                    reserved = self.budget.reserve_jev(len(encoded.encode('utf-8'))+1024, role=role)
                except ProviderError:
                    call.failure_category = 'budget_exhausted'
                    raise
                call.attempts = 1
                call.cost_amount, call.cost_currency = str(reserved), 'CNY'
                call.cost_source = 'operator_price_request_reservation'
            elif not self.budget.try_consume():
                call.failure_category = 'budget_exhausted'
                raise ProviderError('daily JEV budget exhausted')
            else:
                call.attempts = 1
            try:
                response = self.session.post(self.base_url+'/v1/systemone', json=payload, timeout=self.timeout_s)
                if response.status_code >= 400:
                    raise ProviderError('JEV HTTP '+str(response.status_code))
                body = response.json()
            except requests.exceptions.Timeout:
                raise ProviderTimeout('JEV timeout') from None
            except requests.exceptions.RequestException:
                raise ProviderError('JEV network request failed') from None
            except ValueError:
                raise ProviderError('JEV invalid JSON') from None
            if not isinstance(body, dict) or not isinstance(body.get('model'), str) or not isinstance(body.get('answers'), dict):
                raise ProviderError('JEV invalid response envelope')
            if self.model not in ('jev-latest', 'jev-preview') and body['model'] != self.model:
                raise ProviderError('JEV response model differs from pinned version')
            answers = body['answers']
            if set(answers) - set(specs) or require_all and set(answers) != set(specs):
                raise ProviderError('JEV missing/unexpected answers')
            for qid, answer in answers.items():
                validate_answer(specs[qid], answer)
            usage = body.get('usage')
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens','output_tokens')):
                raise ProviderError('JEV missing/invalid usage')
            call.input_tokens, call.output_tokens = usage['input_tokens'], usage['output_tokens']
            if hasattr(self.budget, 'jev_cost'):
                actual = self.budget.jev_cost(call.input_tokens, call.output_tokens)
                self.budget.reconcile_jev(reserved, actual)
                call.cost_amount, call.cost_source = str(actual), 'operator_verified_token_prices'
            call.status, call.model = 'ok', body['model']
            self.breaker.record_success()
            self.last_call = call
            return body
        except Exception as exc:
            self.breaker.record_failure()
            call.status = 'timeout' if isinstance(exc, ProviderTimeout) else 'error'
            call.error = type(exc).__name__
            raise
        finally:
            call.latency_ms = int((time.monotonic()-started)*1000)
            if self.auditor:
                self.auditor.save(call)

    @staticmethod
    def probe_input():
        return {'state': {'probe': True, 'fact': 'probe'}, 'questions': [{
            'question_id': 'thesis_supported', 'type': 'noul',
            'instructions': 'Does the state contain fact equal to probe?'}]}

    def probe(self):
        self.capability_verified = False
        if not self.api_key:
            return {'ok': False, 'status_code': 0, 'error': 'api_key_missing', 'model': self.model}
        try:
            body = self.evaluate(**self.probe_input(), trace_id=new_id('probe'), role='jev_probe')
            if probability(body['answers']['thesis_supported']['noul']) < Decimal('0.8'):
                raise ProviderError('probe fact check failed')
            self.capability_verified = True
            return {'ok': True, 'status_code': 200, 'model': body['model'],
                    'latency_ms': self.last_call.latency_ms, 'usage': body['usage'], 'answers_count': len(body['answers'])}
        except Exception as exc:
            status = 401 if '401' in str(exc) else 408 if isinstance(exc, ProviderTimeout) else 0
            return {'ok': False, 'status_code': status, 'error': 'JEV probe failed: '+type(exc).__name__+ (' (401)' if status==401 else ''), 'model': self.model}

    def judge(self, *, context_id, thesis_id, instrument_id, as_of, questions, trace_id, state=None, thesis=None):
        body = self.evaluate(state=state, questions=questions, trace_id=trace_id, require_all=False)
        judgments = []
        for q in questions:
            qid = q.get('question_id') or q.get('id')
            ans = body['answers'].get(qid)
            if ans is None:
                judgments.append(Judgment(question_id=qid, missing_reason='question_missing_in_jev_response'))
                continue
            kind = ans['type']
            value = ans['choice'] if kind=='choice' else str(ans['score']) if kind=='score' else ('yes' if ans['noul']>=0.5 else 'no')
            judgments.append(Judgment(question_id=qid, value=value,
                probability=ans['noul'] if kind=='noul' else ans['confidence'],
                probability_semantics=ConfidenceSemantics.QUESTION_YES_PROBABILITY if kind=='noul' else ConfidenceSemantics.ANSWER_DISTRIBUTION_CONFIDENCE))
        return JudgmentBatch(id=new_id('jb'), trace_id=trace_id, created_at=utcnow(),
            context_id=context_id, thesis_id=thesis_id, instrument_id=instrument_id,
            provider=self.kind, model=body['model'], question_version=self.question_version,
            judgments=judgments, latency_ms=self.last_call.latency_ms,
            status='ok' if all(j.missing_reason is None for j in judgments) else 'partial', as_of=as_of)
