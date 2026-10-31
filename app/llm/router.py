"""Model routing (§B12.2): gpt-4o-mini for simple questions, gpt-4o for complex / multi-hop / analytical ones
and for tool loops that reach a second step; gpt-4o timeouts and 5xx errors (after one retry) fall back to
gpt-4o-mini.

Not to be confused with `app.retrieval.router`, which plans retrieval (intent, entities, rewritten query)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import openai
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.observability.langfuse import llm_callbacks
from app.retrieval.router import QueryPlan

log = logging.getLogger(__name__)

STRONG_INTENTS = {"multi_hop", "analytical"}
STRONG_FROM_STEP = 2  # "any tool loop with >= 2 steps"
FALLBACK_ERRORS = (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)


def choose_model(plan: QueryPlan | None, steps: int, fast: str, strong: str) -> str:
    if plan is None or plan.intent == "chit_chat":
        return fast
    if plan.complexity == "complex" or plan.intent in STRONG_INTENTS or steps >= STRONG_FROM_STEP:
        return strong
    return fast


class ModelRouter:
    def __init__(self, fast: str, strong: str, factory: Callable[[str], ChatOpenAI]):
        self.fast, self.strong = fast, strong
        self._factory = factory
        self._models: dict[str, ChatOpenAI] = {}

    @classmethod
    def from_settings(cls, s: Settings) -> ModelRouter:
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else None

        def factory(model: str) -> ChatOpenAI:
            # The strong model gets one retry before the fallback; the fast model keeps the usual two.
            return ChatOpenAI(model=model, temperature=0, api_key=key, timeout=30,
                              max_retries=1 if model == s.model_strong else 2)
        return cls(s.model_fast, s.model_strong, factory)

    def choose(self, plan: QueryPlan | None, steps: int = 0) -> str:
        return choose_model(plan, steps, self.fast, self.strong)

    def llm(self, model: str) -> ChatOpenAI:
        if model not in self._models:
            self._models[model] = self._factory(model)
        return self._models[model]

    async def ainvoke(self, model: str, messages: Sequence[BaseMessage], *, tools: Sequence[BaseTool] = (),
                      run_name: str) -> tuple[AIMessage, str, bool]:
        """(reply, model that answered, whether it fell back to the fast model)."""
        async def call(m: str) -> AIMessage:
            llm = self.llm(m)
            runnable = llm.bind_tools(list(tools)) if tools else llm
            return await runnable.ainvoke(list(messages), config={"callbacks": llm_callbacks(),
                                                                  "run_name": run_name})
        try:
            return await call(model), model, False
        except FALLBACK_ERRORS as e:
            if model == self.fast:
                raise
            log.warning("%s failed (%s): falling back to %s", model, type(e).__name__, self.fast)
            return await call(self.fast), self.fast, True
