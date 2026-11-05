from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.integrations.geocoding.base import GeocodingCandidate


@dataclass
class PendingOrigin:
    trip_id: UUID
    origin_text: str
    candidates: list[GeocodingCandidate] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


class PendingOriginStore:
    """In-memory store for pending origin resolution updates awaiting user confirmation."""

    def __init__(self):
        self._store: dict[UUID, PendingOrigin] = {}

    def _normalize(self, trip_id: UUID | str) -> UUID:
        return trip_id if isinstance(trip_id, UUID) else UUID(str(trip_id))

    def set(
        self, trip_id: UUID | str, origin_text: str, candidates: list[GeocodingCandidate]
    ) -> PendingOrigin:
        normalized_id = self._normalize(trip_id)
        record = PendingOrigin(
            trip_id=normalized_id,
            origin_text=origin_text,
            candidates=candidates,
        )
        self._store[normalized_id] = record
        return record

    def get(self, trip_id: UUID | str) -> PendingOrigin | None:
        return self._store.get(self._normalize(trip_id))

    def delete(self, trip_id: UUID | str) -> None:
        self._store.pop(self._normalize(trip_id), None)

    def clear(self) -> None:
        self._store.clear()


default_pending_origin_store = PendingOriginStore()
