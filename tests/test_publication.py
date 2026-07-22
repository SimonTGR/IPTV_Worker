import json
from pathlib import Path

import pytest

from cloud.publication import PUBLIC_EPG_URL, PublicationError, build_public_playlists


def _fixture(root: Path, published: bool = True) -> None:
    output = root / "output"
    (output / "epg").mkdir(parents=True)
    (output / "user_result.m3u").write_text(
        "#EXTM3U x-tvg-url=\"https://private.invalid/epg.gz\"\n"
        "#EXTINF:-1 group-title=\"央视\",CCTV-1\n"
        "https://iptv-cz.example.workers.dev/live/1.m3u8\n"
        "#EXTINF:-1 group-title=\"央视\",CCTV-2\n"
        "#EXTVLCOPT:http-user-agent=demo\n"
        "http://183.10.180.73:202/live/2.m3u8\n",
        encoding="utf-8",
    )
    (output / "report.json").write_text(
        json.dumps({"published": published, "generated_at": "2026-07-22T00:00:00Z"}),
        encoding="utf-8",
    )
    (output / "epg" / "epg.gz").write_bytes(b"epg")


def test_builds_direct_and_full_public_outputs(tmp_path):
    _fixture(tmp_path)
    status = build_public_playlists(tmp_path)

    direct = (tmp_path / "public_output" / "live.m3u").read_text(encoding="utf-8")
    full = (tmp_path / "public_output" / "full.m3u").read_text(encoding="utf-8")
    assert PUBLIC_EPG_URL in direct
    assert "private.invalid" not in direct
    assert "workers.dev" not in direct
    assert "183.10.180.73" in direct
    assert "workers.dev" in full
    assert (tmp_path / "public_output" / "epg.xml.gz").read_bytes() == b"epg"
    assert status == {
        "schema_version": 1,
        "generated_at": "2026-07-22T00:00:00Z",
        "published": True,
        "direct_channel_count": 1,
        "full_channel_count": 2,
        "excluded_from_direct": 1,
    }


def test_refuses_unpublished_results(tmp_path):
    _fixture(tmp_path, published=False)
    with pytest.raises(PublicationError, match="unverified"):
        build_public_playlists(tmp_path)


def test_chaozhou_uses_discovered_direct_base_but_guangdong_keeps_worker_proxy():
    root = Path(__file__).resolve().parents[1]
    sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
    by_id = {source["id"]: source for source in sources}
    assert by_id["worker-chaozhou"]["output_url"] == "redirect"
    assert by_id["worker-guangdong"]["output_url"] == "worker"
