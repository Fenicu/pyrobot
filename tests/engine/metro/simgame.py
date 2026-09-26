"""Транспорт на симуляторе метро: вход и бафы — настоящие кадры, лабиринт — `MazeGame`."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.engine.metro.grid import DIRS
from app.engine.pipeline import Pipeline
from tests.engine.fakegame import FakeGame, Sent
from tests.engine.metro.sim import Frame, Maze, MazeGame

RUN = 3624441
OFFICE = ("screens", 3623175)
MAZE_CLICKS = frozenset(
    {
        *(f"maze_{d}" for d in DIRS),
        "maze_start",
        "maze_continue",
        "maze_first_aid",
        "maze_first_aid_accept",
        "maze_first_aid_decline",
        "maze_npc_low_accept",
        "maze_npc_low_decline",
        "maze_npc_high_accept",
        "maze_npc_high_decline",
        "maze_chest_accept",
        "maze_chest_decline",
        "maze_exit_accept",
        "maze_exit_decline",
    }
)


def enter_with_real_frames(game: FakeGame) -> None:
    """🏢Офис → 🚇Метро → вход → три бафа за 🕳: настоящие кадры живого забега."""
    game.on_text("🏢Офис", OFFICE)
    game.on_text("🚇Метро", ("metro", RUN, 0))
    game.on_click("maze_enter_accept", edit=("metro", RUN, 1))
    for version, buff in ((2, "fastMove"), (3, "strong"), (4, "firstAid")):
        game.on_click(f"maze_buf_tokens_{buff}", edit=("metro", RUN, version))


class SimGame(FakeGame):
    def __init__(self, pipeline: Pipeline, maze: Maze, **game: Any) -> None:
        super().__init__(pipeline)
        self.sim = MazeGame(maze, **game)
        enter_with_real_frames(self)

    async def click(
        self, chat_id: int, message_id: int, data: str, timeout_s: float
    ) -> str | None:
        if data not in MAZE_CLICKS:
            return await super().click(chat_id, message_id, data, timeout_s)
        self.sent.append(Sent("click", data, message_id))
        frames = [Frame(self.sim.start())] if data == "maze_start" else self.sim.click(data)
        task = asyncio.ensure_future(self._edits(message_id, frames))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return None

    async def _edits(self, message_id: int, frames: list[Frame]) -> None:
        await asyncio.sleep(0)
        async with self._lock:
            for frame in frames:
                original = self.messages[message_id]
                text, buttons = frame.screen()
                now = datetime.now(UTC)
                await self._push(
                    replace(
                        original,
                        text=text,
                        inline=buttons,
                        kind="edit",
                        revision=next(self._revisions),
                        date=now,
                        received_at=now,
                        created_at=original.origin,
                    )
                )
