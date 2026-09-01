"""Tier-3 LLM adapter. Provider-agnostic; the harness/tests use MockLLM (keyword +
similarity heuristics, deterministic, offline). AnthropicLLM is the production
adapter — same interface, batched few-shot prompts, JSON out.
"""
from __future__ import annotations

import json
import os

from . import taxonomy


class LLMAdapter:
    def classify(self, line: dict, client_context: dict) -> dict:
        """line: {narration, normalized, direction, amount, channel, counterparty_name}
        client_context: {business, ledgers: [{guid, name, gloss, category_id}],
                         examples: [(normalized, ledger_name)]}
        → {category_id, ledger_guid|None, raw_score, rationale}"""
        raise NotImplementedError


class MockLLM(LLMAdapter):
    """Deterministic offline stand-in: taxonomy keywords + channel priors.
    Exists so the pipeline and harness run anywhere; production uses AnthropicLLM."""

    CHANNEL_PRIOR = {
        "bank_charges": "bank_charges", "bank_interest": "interest_income",
        "salary": "salary_wages", "cash": "cash_deposit", "reversal": "reversal",
    }

    def classify(self, line: dict, client_context: dict) -> dict:
        cat, strength = taxonomy.keyword_category(
            line["narration"] + " " + line.get("counterparty_name", ""), line["direction"])
        if not cat and line["channel"] in self.CHANNEL_PRIOR:
            base = self.CHANNEL_PRIOR[line["channel"]]
            if base == "cash_deposit" and line["direction"] == "dr":
                base = "cash_withdrawal"
            if base == "interest_income" and line["direction"] == "dr":
                base = "interest_paid"
            cat, strength = base, 0.65
        if not cat:  # party fallback: unknown counterparty → trade purchase / receipt
            cat = "vendor_payment" if line["direction"] == "dr" else "customer_receipt"
            strength = 0.45
        return {"category_id": cat, "ledger_guid": None,
                "raw_score": strength, "rationale": f"keyword/channel → {cat}"}


class AnthropicLLM(LLMAdapter):
    """Production Tier 3. Requires ANTHROPIC_API_KEY. Prompt = client business context
    + glossed ledger list + top-k similar labeled examples + the line; JSON response
    {category_id, ledger_guid, confidence, rationale}. Calls are batched upstream."""

    MODEL = os.environ.get("LP_T3_MODEL", "claude-haiku-latest")

    def __init__(self):
        import anthropic  # lazy: only needed in production
        self._client = anthropic.Anthropic()

    def classify(self, line: dict, client_context: dict) -> dict:
        cats = [{"id": c["id"], "gloss": c["gloss"]} for c in taxonomy.load()]
        ledgers = client_context.get("ledgers", [])
        examples = client_context.get("examples", [])[:8]
        prompt = (
            "You classify one Indian bank-statement line into (a) a category and (b) the "
            "best ledger from THIS client's chart of ledgers. Respond with only JSON: "
            '{"category_id": str, "ledger_guid": str|null, "confidence": 0..1, "rationale": str}.\n\n'
            f"Client business: {client_context.get('business', 'unknown')}\n"
            f"Categories: {json.dumps(cats)}\n"
            f"Client ledgers: {json.dumps([{k: l[k] for k in ('guid', 'name', 'gloss')} for l in ledgers])}\n"
            f"Past examples (narration → ledger): {json.dumps(examples)}\n\n"
            f"Line: {json.dumps({k: line[k] for k in ('narration', 'direction', 'amount', 'channel')})}"
        )
        msg = self._client.messages.create(
            model=self.MODEL, max_tokens=300,
            messages=[{"role": "user", "content": prompt}])
        out = json.loads(msg.content[0].text)
        return {"category_id": out.get("category_id", "suspense"),
                "ledger_guid": out.get("ledger_guid"),
                "raw_score": float(out.get("confidence", 0.5)),
                "rationale": out.get("rationale", "")}


def get_adapter() -> LLMAdapter:
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("LP_USE_REAL_LLM") == "1":
        return AnthropicLLM()
    return MockLLM()
