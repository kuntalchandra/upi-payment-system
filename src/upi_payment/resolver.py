from __future__ import annotations

from upi_payment.domain import ResolvedVPA, VPA


class InMemoryVpaResolver:
    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = {VPA.parse(vpa): name for vpa, name in entries.items()}

    def resolve(self, vpa: VPA) -> ResolvedVPA | None:
        display_name = self._entries.get(vpa)
        if display_name is None:
            return None
        return ResolvedVPA(vpa=vpa, display_name=display_name)

