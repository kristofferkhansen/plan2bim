"""Errors that a user can fix. Each message says what to change."""
from __future__ import annotations


class InputError(Exception):
    """The project file or a drawing cannot be used."""


class ProjectError(InputError):
    """The project file is wrong. `problems` holds every problem found, not only the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))
