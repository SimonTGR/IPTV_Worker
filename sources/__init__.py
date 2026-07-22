"""Unified source adapters used by the IPTV update pipeline."""

from sources.base import Candidate, SourceAdapter, SourceResult
from sources.http_playlist import HttpPlaylistAdapter
from sources.worker_discovery import WorkerDiscoveryAdapter
from sources.registry import (
    SourceCollection,
    SourceRegistry,
    collect_configured_sources,
    refresh_configured_source_once,
)

__all__ = [
    "Candidate",
    "SourceAdapter",
    "SourceResult",
    "HttpPlaylistAdapter",
    "WorkerDiscoveryAdapter",
    "SourceCollection",
    "SourceRegistry",
    "collect_configured_sources",
    "refresh_configured_source_once",
]
