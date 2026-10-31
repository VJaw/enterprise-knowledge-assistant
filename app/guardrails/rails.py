"""NeMo Guardrails wiring (§B10.3): `LLMRails` built from app/guardrails/config, with the custom actions of
app/guardrails/actions.py registered.

NeMo wraps the pipeline rather than generating the answer: the pipeline calls `check_input` before the agent
and `check_output` after it (`generate_async` with only the input or only the output rails enabled). The
`self check input` rail runs from its own `LLMRails`, so the pipeline can run it alongside the agent: it is a
gpt-4o-mini call (~0.8-2 s) and would otherwise sit in front of every request (D-023). NeMo 0.24 ignores
per-call flow lists, hence the two instances built from one config.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from nemoguardrails import LLMRails, RailsConfig

from app.core.types import RetrievedChunk
from app.guardrails import actions
from app.guardrails.actions import Judgement, current, rail_span
from app.guardrails.pii import get_masker

CONFIG_DIR = Path(__file__).parent / "config"
SELF_CHECK_INPUT = "self check input"
BLOCKED = "GUARDRAIL_BLOCKED"  # §B11.3: a refusal is a normal 200 response carrying this flag
ONLY_INPUT = {"input": True, "output": False, "dialog": False, "retrieval": False}
REFUSAL = ("I can't help with that request. I can answer questions about Acme's policies, systems, "
           "procedures, incidents and data.")  # same text as `bot refuse to respond` in rails.co
ONLY_OUTPUT = {"input": False, "output": True, "dialog": False, "retrieval": False}


@dataclass
class Verdict:
    blocked: bool
    text: str  # the masked message / final answer, or the refusal when blocked
    rail: str | None = None  # the rail that blocked
    suspected: bool = False  # input only: DeBERTa flagged it, so the self-check must run before the agent


def _with_input_flows(config: RailsConfig, keep) -> RailsConfig:
    c = config.model_copy(deep=True)
    c.rails.input.flows = [f for f in config.rails.input.flows if keep(f)]
    return c


class Guardrails:
    def __init__(self, llm, judge=None, config_dir: Path = CONFIG_DIR):
        config = RailsConfig.from_path(str(config_dir))
        self.input_rails = LLMRails(_with_input_flows(config, lambda f: f != SELF_CHECK_INPUT), llm=llm)
        self.self_check_rails = LLMRails(_with_input_flows(config, lambda f: f == SELF_CHECK_INPUT), llm=llm)
        self.output_rails = LLMRails(config, llm=llm)
        self.judge = judge.with_structured_output(Judgement) if judge is not None else None

        async def groundedness_backstop(answer: str) -> str:
            return await actions.groundedness_backstop(answer, self.judge)

        registry = {f.__name__: f for f in (
            actions.llm_guard_injection, actions.llm_guard_toxicity_input, actions.mask_pii_input,
            actions.check_topic_policy, actions.enforce_citations, actions.mask_pii_output,
            actions.llm_guard_toxicity_output, actions.check_output_policy, actions.needs_output_self_check)}
        registry["groundedness_backstop"] = groundedness_backstop
        for rails in (self.input_rails, self.self_check_rails, self.output_rails):
            for name, fn in registry.items():
                rails.register_action(fn, name=name)

    def warm_up(self) -> None:
        """Load Presidio / spaCy and the LLM Guard models and run each once (first calls are slow)."""
        get_masker().analyze("warm up jane@example.com", ["EMAIL_ADDRESS"])
        s = actions.get_scanners()
        s.injection_score("warm up")
        s.toxicity_score("warm up")
        s.topic_scores("warm up")

    # ------------------------------------------------------------------ rails

    @staticmethod
    async def _run(rails: LLMRails, messages: list[dict], which: dict) -> Verdict:
        res = await rails.generate_async(messages=messages, options={
            "rails": which, "output_vars": ["user_message", "bot_message"], "log": {"activated_rails": True}})
        stopped = next((r.name for r in res.log.activated_rails if r.stop), None)
        content = res.response[0]["content"] if res.response else ""
        if stopped:
            return Verdict(blocked=True, text=content, rail=stopped)
        data = res.output_data or {}
        key = "user_message" if which is ONLY_INPUT else "bot_message"
        return Verdict(blocked=False, text=data.get(key) or content)

    async def check_input(self, message: str) -> Verdict:
        """Injection + toxicity scans, PII masking, topic policy. Returns the masked message."""
        gs = current()
        with rail_span("guardrails.input", timing="input") as span:
            v = await self._run(self.input_rails, [{"role": "user", "content": message}], ONLY_INPUT)
            v.suspected = "injection_suspected" in gs.flags
            if v.blocked:
                gs.flag(BLOCKED)
            span.update(input={"message": v.text if not v.blocked else "(blocked)"},
                        output={"blocked": v.blocked, "rail": v.rail, "flags": list(gs.flags)})
        return v

    async def self_check_input(self, masked: str) -> Verdict:
        """NeMo `self check input` (gpt-4o-mini) on the masked message. An LLM error lets the message through
        (flagged), unless DeBERTa already suspected it: then it fails closed."""
        gs = current()
        with rail_span("guardrails.self_check_input", timing="self_check_input") as span:
            try:
                v = await self._run(self.self_check_rails, [{"role": "user", "content": masked}], ONLY_INPUT)
            except Exception as e:  # noqa: BLE001
                gs.flag("self_check_failed")
                fail_closed = "injection_suspected" in gs.flags
                v = Verdict(blocked=fail_closed, text=masked, rail=SELF_CHECK_INPUT if fail_closed else None)
                span.update(level="WARNING", status_message=type(e).__name__)
            if v.blocked:
                gs.flag("self_check_input_blocked", BLOCKED)
                if v.text == masked:  # failed closed: no refusal text from NeMo
                    v.text = REFUSAL
            span.update(output={"blocked": v.blocked})
        return v

    async def check_output(self, masked_question: str, answer: str) -> Verdict:
        """Groundedness backstop, citation enforcement, PII re-scan, toxicity / topics, output policy, and
        the gated LLM self-check. `GuardState.contexts / grounding / intent` must be set first."""
        gs = current()
        with rail_span("guardrails.output", timing="output") as span:
            v = await self._run(self.output_rails, [{"role": "user", "content": masked_question},
                                                    {"role": "assistant", "content": answer}], ONLY_OUTPUT)
            if v.blocked:
                gs.flag(BLOCKED)
            span.update(output={"blocked": v.blocked, "rail": v.rail, "answer": v.text})
        return v

    # ------------------------------------------------------------------ untrusted content (agent side)

    async def scan_chunks(self, chunks: list[RetrievedChunk], name: str) -> list[RetrievedChunk]:
        return await actions.scan_chunks(chunks, name)

    async def scan_text(self, text: str, source: str) -> str:
        return await actions.scan_untrusted(text, source)

    async def prescan_output(self, text: str) -> None:
        await actions.prescan_output(text)


async def build_guardrails(llm, judge=None) -> Guardrails:
    g = Guardrails(llm, judge)
    await asyncio.to_thread(g.warm_up)
    return g
