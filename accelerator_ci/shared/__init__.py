"""Shared utilities used across cluster-provision, operators, and tests."""

import time
from typing import Generator


def adaptive_sleep(initial: float, factor: float, maximum: float) -> Generator[None, None, None]:
    """Yields after sleeping with exponential backoff. All params required."""
    delay = initial
    while True:
        time.sleep(delay)
        yield
        delay = min(delay * factor, maximum)
