"""Retry logic with exponential backoff for HTTP transports."""

import random
import time
from typing import Any, Callable, Optional, Set, TypeVar

T = TypeVar("T")

RETRYABLE_STATUS_CODES: Set[int] = {429, 500, 502, 503, 504}


class RetryError(RuntimeError):
    """All retry attempts exhausted."""

    def __init__(self, last_error: Exception, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"Failed after {attempts} attempts: {last_error}")


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    jitter: bool = True,
    retryable_exceptions: Optional[tuple] = None,
) -> T:
    """Execute fn with exponential backoff retry.

    Retries on:
      - RuntimeError subclasses containing HTTP status codes 429, 5xx
      - Any exception in retryable_exceptions tuple

    Args:
        fn: Zero-arg callable to retry.
        max_attempts: Total attempts (including the first).
        base_delay: Base delay in seconds (doubled each retry).
        jitter: Add random jitter to prevent thundering herd.
        retryable_exceptions: Extra exception types to retry on.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if base_delay < 0:
        raise ValueError("base_delay must be >= 0")

    last_error: Optional[Exception] = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e

            # Check if we should retry.
            should_retry = False

            # Check retryable exceptions.
            if retryable_exceptions and isinstance(e, retryable_exceptions):
                should_retry = True

            # Check for HTTP status codes in error message.
            err_str = str(e)
            for code in RETRYABLE_STATUS_CODES:
                if f"HTTP {code}" in err_str or f"{code}" in err_str:
                    should_retry = True
                    break

            if not should_retry or attempt == max_attempts - 1:
                raise

            # Exponential backoff.
            delay = base_delay * (2 ** attempt)
            if jitter:
                delay *= 0.5 + random.random()
            time.sleep(delay)

    raise RetryError(last_error, max_attempts)
