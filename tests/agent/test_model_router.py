"""Model routing (§B12.2): which model answers, and the gpt-4o -> gpt-4o-mini fallback."""
import httpx
import openai
import pytest

from app.llm.router import choose_model
from tests.fakes import Script, fake_models, plan


@pytest.mark.parametrize(("intent", "complexity", "steps", "model"), [
    ("factual", "simple", 0, "gpt-4o-mini"),
    ("structured_data", "simple", 1, "gpt-4o-mini"),
    ("factual", "simple", 2, "gpt-4o"),  # a tool loop reaching its 2nd step
    ("factual", "complex", 0, "gpt-4o"),
    ("multi_hop", "simple", 0, "gpt-4o"),
    ("analytical", "simple", 0, "gpt-4o"),
    ("chit_chat", "complex", 3, "gpt-4o-mini"),
])
def test_choose_model(intent, complexity, steps, model):
    assert choose_model(plan(intent=intent, complexity=complexity), steps, "gpt-4o-mini", "gpt-4o") == model


def timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))


async def test_strong_model_errors_fall_back_to_the_fast_model():
    script = Script([timeout(), "fallback answer"])
    reply, used, fell_back = await fake_models(script).ainvoke("gpt-4o", [], run_name="agent.step_1")
    assert (reply.content, used, fell_back) == ("fallback answer", "gpt-4o-mini", True)
    assert [c["model"] for c in script.calls] == ["gpt-4o", "gpt-4o-mini"]


async def test_fast_model_errors_propagate():
    with pytest.raises(openai.APITimeoutError):
        await fake_models(Script([timeout()])).ainvoke("gpt-4o-mini", [], run_name="x")


async def test_fallback_is_flagged_in_the_agent():
    from app.agent.graph import run_config, turn_input
    from tests.fakes import FakeRouter, build_agent

    router = FakeRouter(lambda m: plan(m, intent="multi_hop", complexity="complex"))
    out = await build_agent(Script([timeout(), "A [1]."]), router=router).ainvoke(turn_input("q"),
                                                                                  config=run_config("s"))
    assert out["model"] == "gpt-4o-mini" and out["flags"] == ["model_fallback"]
