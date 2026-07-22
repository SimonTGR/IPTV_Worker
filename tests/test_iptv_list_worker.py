from pathlib import Path


WORKER = Path(__file__).resolve().parents[1] / "workers" / "iptv_list.js"


def test_final_playlist_worker_has_only_expected_routes_and_r2_binding():
    source = WORKER.read_text(encoding="utf-8")
    assert 'PLAYLIST_KEY = "output/user_result.m3u"' in source
    assert 'REPORT_KEY = "output/report.json"' in source
    assert "env.IPTV_BUCKET.get" in source
    assert 'path === "/m3u"' in source
    assert 'path === "/report"' in source
    assert 'path === "/health"' in source
    assert "fetch(" not in source.replace("async fetch(request, env)", "")


def test_final_playlist_worker_requires_secret_for_playlist_and_report():
    source = WORKER.read_text(encoding="utf-8")
    assert "env.PLAYLIST_TOKEN" in source
    assert "supplied === env.PLAYLIST_TOKEN" in source
    assert 'if (!authorized(request, env))' in source
    assert '"If-None-Match"' in source
    assert '"ETag"' in source


def test_example_does_not_contain_real_secrets():
    source = (WORKER.parent / "iptv-list.wrangler.toml.example").read_text(encoding="utf-8")
    assert "PLAYLIST_TOKEN =" not in source
    assert "account_id" not in source
    assert 'binding = "IPTV_BUCKET"' in source
