from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Candidate:
    """A channel URL with enough provenance to survive aggregation."""

    channel_name: str
    canonical_name: str
    url: str
    source_id: str
    source_type: str
    source_priority: int
    discovered_at: str
    headers: dict[str, str] | None = None
    tvg_logo: str | None = None
    source_path: str | None = None
    dynamic_base: bool = False

    def to_channel_data(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "headers": dict(self.headers) if self.headers else None,
            "tvg_logo": self.tvg_logo,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_priority": self.source_priority,
            "discovered_at": self.discovered_at,
            "source_path": self.source_path,
            "dynamic_base": self.dynamic_base,
        }


@dataclass
class SourceResult:
    source_id: str
    source_type: str
    success: bool = True
    status: str = "success"
    candidates: list[Candidate] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    state_data: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_report(self) -> dict[str, Any]:
        from sources.http_client import sanitize_report_value

        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "success": self.success,
            "status": self.status,
            "candidate_count": len(self.candidates),
            "files": sanitize_report_value(self.files),
            "errors": sanitize_report_value(self.errors),
            "metadata": sanitize_report_value(self.metadata),
        }


class SourceAdapter(ABC):
    def __init__(self, source: dict[str, Any], base_dir: str, state: dict[str, Any] | None = None):
        self.source = source
        self.base_dir = base_dir
        self.source_id = source["id"]
        self.source_type = source["type"]
        self.priority = int(source.get("priority", 0))
        self.state = state or {}

    @abstractmethod
    def collect(self, force_refresh: bool = False) -> SourceResult:
        """Discover candidates without mutating the upstream source."""


def candidate_asdict(candidate: Candidate) -> dict[str, Any]:
    return asdict(candidate)
