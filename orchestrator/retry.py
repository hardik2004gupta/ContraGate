"""
Retry logic and exponential backoff for ContraGate orchestrator.

Retry policies (CLAUDE.md §10):
  Sandbox timeout:
    - Attempt 1: immediate retry after base_delay
    - Attempt 2: exponential backoff (base_delay * backoff_factor)
    - After 2 failures: continue with simulation_available=False, timeout_occurred=True

  Memory store unavailable:
    - No retry — continue immediately with retrieval_available=False

  LLM API failures:
    - Up to 3 retries with exponential backoff
    - After 3 failures: raise — pipeline cannot continue without LLM summarization

  General policy:
    - Do NOT retry policy gate rejections (intentional auto-rejects)
    - Do NOT retry PERMANENT reversibility escalations (correct classifications)
    - Retry only transient infrastructure failures (timeouts, connection errors)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 2,
    base_delay_seconds: float = 1.0,
    backoff_factor: float = 2.0,
    timeout_seconds: float = 5.0,
    **kwargs: Any,
) -> Any:
    """
    Call fn(*args, **kwargs) up to max_attempts times with exponential backoff.

    On each failure, waits base_delay_seconds * (backoff_factor ** attempt) before retry.
    If all attempts fail, raises the last exception.

    Used for sandbox retries (CLAUDE.md §10: 'retry twice with exponential backoff').
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout_seconds)
            else:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: fn(*args, **kwargs)),
                    timeout=timeout_seconds,
                )
            return result
        except asyncio.TimeoutError as exc:
            last_exc = exc
            delay = base_delay_seconds * (backoff_factor ** attempt)
            logger.warning(
                "Attempt %d/%d timed out after %.1fs — retrying in %.1fs",
                attempt + 1, max_attempts, timeout_seconds, delay,
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(delay)
        except Exception as exc:
            last_exc = exc
            delay = base_delay_seconds * (backoff_factor ** attempt)
            logger.warning(
                "Attempt %d/%d failed: %s — retrying in %.1fs",
                attempt + 1, max_attempts, str(exc), delay,
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(delay)

    raise last_exc or RuntimeError("All retry attempts exhausted")
