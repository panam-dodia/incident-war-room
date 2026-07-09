"""Thin wrapper around Qwen Cloud's DashScope OpenAI-compatible API.

If QWEN_API_KEY is unset, every call is routed to a caller-supplied mock
generator instead of hitting the network. This lets the whole coordination
system (bidding, negotiation, evaluation) run and be demoed with zero
credentials, and swapping to real Qwen Cloud later is just setting env vars.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

from app.models import UsageStats

MODEL_ENV = {
    "bid": "QWEN_MODEL_BID",
    "negotiate": "QWEN_MODEL_NEGOTIATE",
    "judge": "QWEN_MODEL_JUDGE",
    "baseline": "QWEN_MODEL_BASELINE",
}

DEFAULT_MODELS = {
    "bid": "qwen3.6-flash",
    "negotiate": "qwen3.7-plus",
    "judge": "qwen3.7-max",
    "baseline": "qwen3.7-plus",
}

# A single judge sample was measured to flip-flop on genuinely close calls (inc-03:
# correct in one run, wrong in another) even at low temperature -- a low-temperature
# single sample just deterministically reproduces whatever the model's momentary
# reasoning leans toward, which isn't necessarily stable when the case is genuinely
# close. The fix that's actually evidenced for this (self-consistency / majority-vote
# LLM-as-judge research) is the opposite: sample several times with real diversity and
# take the majority verdict (see negotiation.py's judge_votes()), which needs a higher
# temperature to produce meaningfully different independent samples to vote across.
DEFAULT_TEMPERATURE = {
    "bid": 0.4,
    "negotiate": 0.4,
    "judge": 0.6,
    "baseline": 0.4,
}

# Blended (input+output average) per-1k-token USD list pricing, used only to surface a
# cost estimate in the dashboard/eval comparison -- not billing-accurate. Source:
# https://docs.qwencloud.com/developer-guides/getting-started/pricing (<=256K token tier)
COST_PER_1K_TOKENS = {
    "qwen3.6-flash": 0.0009,
    "qwen3.7-plus": 0.0010,
    "qwen3.7-max": 0.0050,
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class QwenClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("QWEN_API_KEY", "").strip()
        self.mock_mode = not self.api_key
        self._client = None
        if not self.mock_mode:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=os.getenv(
                    "QWEN_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
                timeout=120.0,  # real reasoning-heavy calls can take 60-100s+; still far short of the SDK's 600s default
                max_retries=1,
            )

    def model_for(self, tier: str) -> str:
        return os.getenv(MODEL_ENV[tier], DEFAULT_MODELS[tier])

    def temperature_for(self, tier: str) -> float:
        return DEFAULT_TEMPERATURE[tier]

    def complete_json(
        self,
        tier: str,
        system_prompt: str,
        user_prompt: str,
        mock_fn: Callable[[], dict],
    ) -> tuple[dict, UsageStats]:
        """Return (parsed_json_response, usage). Falls back to mock_fn() with no
        network call when running without credentials."""
        start = time.perf_counter()
        model = self.model_for(tier)

        if self.mock_mode:
            result = mock_fn()
            tokens = _estimate_tokens(system_prompt + user_prompt + json.dumps(result))
            # small synthetic delay so live-streamed demos don't feel instantaneous/fake
            latency_ms = (time.perf_counter() - start) * 1000 + 60
            usage = UsageStats(
                tokens_used=tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=tokens / 1000 * COST_PER_1K_TOKENS.get(model, 0.0008),
                calls_made=1,
            )
            return result, usage

        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                    + "\nRespond with a single valid JSON object only, no prose, no markdown fences.",
                },
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature_for(tier),
        )
        latency_ms = (time.perf_counter() - start) * 1000
        content = response.choices[0].message.content or ""

        # response_format=json_object only guarantees *valid JSON*, not that it matches
        # our expected keys/shape (models sometimes rename a field or return a list where
        # a string was asked for). mock_fn()'s output is always shape-correct, so use it
        # as a schema-safe default and overlay whatever the real model actually returned
        # on top, rather than crashing the whole run on one malformed field.
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                parsed = {}
        except json.JSONDecodeError:
            parsed = {}
        defaults = mock_fn()
        result = {**defaults, **{k: v for k, v in parsed.items() if v is not None}}

        usage_obj = response.usage
        tokens = usage_obj.total_tokens if usage_obj else _estimate_tokens(system_prompt + user_prompt + content)
        usage = UsageStats(
            tokens_used=tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=tokens / 1000 * COST_PER_1K_TOKENS.get(model, 0.0008),
            calls_made=1,
        )
        return result, usage

    def complete_with_tools(
        self,
        tier: str,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict],
        tool_executor: Callable[[str, dict], str],
        mock_fn: Callable[[], dict],
        answer_schema: str,
        max_tool_calls: int = 5,
    ) -> tuple[dict, UsageStats, list[str]]:
        """Real multi-turn tool-calling loop: the model can request tools (up to
        max_tool_calls total invocations), each executed via tool_executor(name, args)
        -> result text, until it stops requesting tools or the budget runs out. A final
        call (no tools, JSON mode) then extracts the structured answer informed by
        whatever it learned. Returns (result, usage, names_of_tools_actually_called).

        In mock mode, returns mock_fn() directly with no tool calls -- the budget
        mechanic only matters for testing real model behavior, not for demoing the
        deterministic mock pipeline.
        """
        start = time.perf_counter()
        model = self.model_for(tier)

        if self.mock_mode:
            result = mock_fn()
            tokens = _estimate_tokens(system_prompt + user_prompt + json.dumps(result))
            latency_ms = (time.perf_counter() - start) * 1000 + 60
            usage = UsageStats(
                tokens_used=tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=tokens / 1000 * COST_PER_1K_TOKENS.get(model, 0.0008),
                calls_made=1,
            )
            return result, usage, []

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        usage_total = UsageStats()
        tools_called: list[str] = []
        temperature = self.temperature_for(tier)

        while len(tools_called) < max_tool_calls:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            usage_obj = response.usage
            tokens = usage_obj.total_tokens if usage_obj else 0
            usage_total = usage_total + UsageStats(
                tokens_used=tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=tokens / 1000 * COST_PER_1K_TOKENS.get(model, 0.0008),
                calls_made=1,
            )
            msg = response.choices[0].message
            requested = msg.tool_calls or []
            if not requested:
                break

            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in requested
            ]})
            for tc in requested:
                if len(tools_called) >= max_tool_calls:
                    result_text = "Investigation budget exhausted -- no more checks available before you must answer."
                else:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result_text = tool_executor(tc.function.name, args)
                    tools_called.append(tc.function.name)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        messages.append(
            {
                "role": "user",
                "content": "Based on everything above, give your final answer now. Respond with a single "
                "valid JSON object only, no prose, no markdown fences, matching exactly this shape:\n"
                + answer_schema,
            }
        )
        final_response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        final_latency_ms = (time.perf_counter() - start) * 1000
        content = final_response.choices[0].message.content or ""
        final_usage_obj = final_response.usage
        final_tokens = final_usage_obj.total_tokens if final_usage_obj else _estimate_tokens(content)
        usage_total = usage_total + UsageStats(
            tokens_used=final_tokens,
            latency_ms=final_latency_ms,
            estimated_cost_usd=final_tokens / 1000 * COST_PER_1K_TOKENS.get(model, 0.0008),
            calls_made=1,
        )

        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                parsed = {}
        except json.JSONDecodeError:
            parsed = {}
        defaults = mock_fn()
        result = {**defaults, **{k: v for k, v in parsed.items() if v is not None}}
        return result, usage_total, tools_called


qwen_client = QwenClient()
