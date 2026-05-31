"""Source protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import RawItem


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self) -> list[RawItem]:
        """Return content items currently available from this source.

        Implementations should be resilient: log and return an empty list on
        failure rather than raising, so one broken source can't sink the run.
        """
        ...
