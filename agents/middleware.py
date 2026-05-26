"""Shared middleware for all Ember AI agents."""

import logging
import time
from datetime import date

from agentscope.agent import Agent
from agentscope.message import Msg
from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)

_CONTENT_TRUNCATE = 500


def _msg_text(msg) -> str:
    """Extract text content from a Msg, truncated for logging."""
    if isinstance(msg, Msg):
        text = msg.get_text_content() or ""
    else:
        text = str(msg)
    if len(text) > _CONTENT_TRUNCATE:
        return text[:_CONTENT_TRUNCATE] + f"...({len(text)} chars)"
    return text


def _input_text(input_kwargs: dict) -> str:
    """Extract user input text from on_reply input_kwargs."""
    inputs = input_kwargs.get("inputs")
    if inputs is None:
        return "(None)"
    if isinstance(inputs, Msg):
        return _msg_text(inputs)
    if isinstance(inputs, list):
        return " | ".join(_msg_text(m) for m in inputs[:3])
    return str(type(inputs).__name__)


class SystemPromptMiddleware(MiddlewareBase):
    """Inject current date into the system prompt."""

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        today = date.today().strftime("%Y-%m-%d")
        return f"{current_prompt}\n\n## 当前日期\n{today}"


class LoggingMiddleware(MiddlewareBase):
    """Log user input, assistant reply, reasoning steps, and tool calls."""

    async def on_reply(self, agent, input_kwargs, next_handler):
        start = time.time()
        user_text = _input_text(input_kwargs)
        logger.info("[%s] input: %s", agent.name, user_text)

        final_text = ""
        async for item in next_handler(**input_kwargs):
            if isinstance(item, Msg):
                final_text = _msg_text(item)
            yield item

        elapsed = time.time() - start
        logger.info("[%s] reply (%.2fs): %s", agent.name, elapsed, final_text)

    async def on_reasoning(self, agent, input_kwargs, next_handler):
        logger.info("[%s] reasoning start", agent.name)
        async for event in next_handler(**input_kwargs):
            yield event
        logger.info("[%s] reasoning end", agent.name)

    async def on_acting(self, agent, input_kwargs, next_handler):
        tool_name = input_kwargs.get("tool_name", "unknown")
        logger.info("[%s] tool call: %s", agent.name, tool_name)
        result = await next_handler(**input_kwargs)
        logger.info("[%s] tool done: %s", agent.name, tool_name)
        return result


class TimingMiddleware(MiddlewareBase):
    """Track and log model call latency."""

    async def on_model_call(self, agent, input_kwargs, next_handler):
        model_name = input_kwargs.get("current_model", None)
        model_str = getattr(model_name, "model", "unknown") if model_name else "unknown"
        start = time.time()
        result = await next_handler()
        elapsed = time.time() - start
        logger.info("[%s] model=%s %.2fs", agent.name, model_str, elapsed)
        return result


class TracingMiddleware(MiddlewareBase):
    """Structured tracing — logs spans with duration for key lifecycle events."""

    async def on_reply(self, agent, input_kwargs, next_handler):
        start = time.time()
        logger.info("[trace] %s reply_start", agent.name)
        async for item in next_handler(**input_kwargs):
            yield item
        elapsed = time.time() - start
        logger.info("[trace] %s reply_end %.2fs", agent.name, elapsed)

    async def on_model_call(self, agent, input_kwargs, next_handler):
        model = input_kwargs.get("current_model")
        model_str = getattr(model, "model", "unknown") if model else "unknown"
        start = time.time()
        result = await next_handler()
        elapsed = time.time() - start
        logger.info("[trace] %s model_call model=%s %.2fs", agent.name, model_str, elapsed)
        return result

    async def on_acting(self, agent, input_kwargs, next_handler):
        tool_name = input_kwargs.get("tool_name", "unknown")
        start = time.time()
        logger.info("[trace] %s tool_start tool=%s", agent.name, tool_name)
        result = await next_handler(**input_kwargs)
        elapsed = time.time() - start
        logger.info("[trace] %s tool_end tool=%s %.2fs", agent.name, tool_name, elapsed)
        return result
