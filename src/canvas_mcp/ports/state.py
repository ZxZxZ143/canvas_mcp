"""Vendor-neutral application state port. Transactions serialize checks per owner."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol

from canvas_mcp.domain.grade_state import GradeFact
from canvas_mcp.domain.models import EntityId


class StateTransaction(Protocol):
    async def initialized(self) -> bool: ...

    async def baseline(self, course_id: EntityId) -> tuple[GradeFact, ...] | None: ...

    async def replace_course(
        self,
        course_id: EntityId,
        facts: tuple[GradeFact, ...],
        checked_at: datetime,
        *,
        baseline: tuple[GradeFact, ...] | None = None,
    ) -> None: ...

    async def mark_checked(self) -> None: ...


class StateRepository(Protocol):
    def transaction(self, owner: str) -> AbstractAsyncContextManager[StateTransaction]: ...

    async def reset(self, owner: str) -> None: ...
