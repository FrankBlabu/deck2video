"""Precise, collectable error reports shared by ``check`` and ``build``."""

from __future__ import annotations

from pydantic import BaseModel


class Problem(BaseModel):
    """One validation failure: where it is and what is wrong."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


class DeckError(Exception):
    """Raised with every problem found, not just the first."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))
