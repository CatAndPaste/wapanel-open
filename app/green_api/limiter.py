import asyncio
from datetime import datetime, timedelta
from aiolimiter import AsyncLimiter


class SmartLimiter(AsyncLimiter):
    """
    aiolimiter + 429 handler
    """

    def __init__(self, rps: float) -> None:
        if rps >= 1:
            super().__init__(max_rate=rps, time_period=1)
            self.period = 1 / rps               # 10 rps -> 0.1s period
        else:
            period = round(1 / rps)             # 0.1 rps -> 10s period
            super().__init__(max_rate=1, time_period=period)
            self.period = period

        self._blocked_until: datetime | None = None

    async def wait_slot(self) -> None:
        until = self._blocked_until
        if until:
            delay = (until - datetime.utcnow()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
        await self.acquire()

    def block(self) -> None:
        """
        1.5 x period wait upon call
        """
        self._blocked_until = datetime.utcnow() + timedelta(seconds=self.period * 1.5)

