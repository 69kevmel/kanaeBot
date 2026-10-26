import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import helpers, tasks


class PlanningRefreshTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_does_not_stop_future_refreshes(self):
        bot = SimpleNamespace(wait_until_ready=AsyncMock())
        with patch.object(helpers, "refresh_event_message", new_callable=AsyncMock) as refresh:
            refresh.side_effect = [PermissionError("Permission temporairement retirée"), object()]
            with self.assertLogs(tasks.logger, level="ERROR"):
                await tasks.auto_refresh_planning.coro(bot)
            await tasks.auto_refresh_planning.coro(bot)
            self.assertEqual(refresh.await_count, 2)

    async def test_loop_can_still_be_cancelled(self):
        bot = SimpleNamespace(wait_until_ready=AsyncMock())
        with patch.object(helpers, "refresh_event_message", new_callable=AsyncMock) as refresh:
            refresh.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await tasks.auto_refresh_planning.coro(bot)


if __name__ == "__main__":
    unittest.main()
