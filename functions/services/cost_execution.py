"""Small deadline helpers for Cost's optional enrichment and bounded storage IO."""

import asyncio
import time


class PriceEnrichmentBudget:
    """One shared deadline, starting at the first optional lookup in an attempt."""

    def __init__(self, seconds, *, cost_deadline=float("inf"), clock=time.monotonic):
        self.seconds = seconds
        self.cost_deadline = cost_deadline
        self.clock = clock
        self.deadline = None

    @property
    def remaining(self):
        if self.deadline is None:
            self.deadline = min(self.clock() + self.seconds, self.cost_deadline)
        return max(0.0, self.deadline - self.clock())

    async def run(self, operation):
        remaining = self.remaining
        if remaining <= 0:
            return None
        try:
            async with asyncio.timeout(remaining):
                return await operation()
        except TimeoutError:
            self.deadline = self.clock()
            return None


async def bounded_storage_call(operation):
    """Do bounded sync SDK IO off-loop, joining it even if the caller cancels.

    Operations must disable SDK retries and supply short RPC timeouts. Never
    abandon a thread that may still be committing an attempt-fenced write.
    """
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Nested deadlines can cancel more than once. Shield every join so a
        # second cancellation cannot detach the still-running SDK thread.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Retrieve a storage failure without masking cancellation.
        raise
