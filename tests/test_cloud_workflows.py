from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_playlist_workflow_is_scheduled_serialized_and_uses_secrets():
    source = (ROOT / ".github/workflows/update-playlist.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "schedule:" in source
    assert "concurrency:" in source
    assert "cancel-in-progress: false" in source
    assert "R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}" in source
    assert "python -m cloud.workflow run" in source
    assert "sudo apt-get install --yes ffmpeg" in source


def test_worker_sync_stays_manual_and_does_not_embed_credentials():
    source = (ROOT / ".github/workflows/sync-workers.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "schedule:" not in source
    assert "--mode manual" in source
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in source
    assert "CLOUDFLARE_API_TOKEN =" not in source


def test_runtime_outputs_are_not_ready_to_commit():
    source = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/pending_sources/*" in source
    assert "output/log/" in source
    assert "workers/.wrangler/" in source
