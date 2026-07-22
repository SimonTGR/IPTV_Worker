import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sources.file_inbox import FileInboxAdapter
from sources.registry import SourceRegistry
from sources.worker_discovery import result_state


class FileInboxTests(unittest.TestCase):
    def _write_registry(self, root: Path, *, required=False) -> Path:
        registry = root / "sources.json"
        registry.write_text(json.dumps({
            "schema_version": 1,
            "sources": [{
                "id": "fixture-inbox",
                "type": "file_inbox",
                "paths": ["inbox/*.m3u", "inbox/*.txt"],
                "priority": 50,
                "required": required,
            }],
        }), encoding="utf-8")
        return registry

    def test_collects_m3u_metadata_without_modifying_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            original = (Path(__file__).parent / "fixtures" / "sample.m3u").read_bytes()
            playlist = inbox / "sample.m3u"
            playlist.write_bytes(original)

            collection = SourceRegistry(self._write_registry(root), root).collect()

            self.assertEqual(original, playlist.read_bytes())
            self.assertEqual(2, len(collection.candidates))
            self.assertEqual("翡翠台", collection.candidates[1].canonical_name)
            self.assertEqual("FixturePlayer/1.0", collection.candidates[0].headers["User-Agent"])
            self.assertEqual(hashlib.sha256(original).hexdigest(), collection.results[0].files[0]["sha256"])
            report = collection.to_report()
            self.assertIn("TVB Jade -> 翡翠台", report["channels"]["翡翠台"]["alias_conversions"])
            self.assertIn("TVB Jade", report["channels"]["翡翠台"]["original_names"])

    def test_decodes_gb18030_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "legacy.txt").write_bytes("广东体育,http://example.test/live\n".encode("gb18030"))

            collection = SourceRegistry(self._write_registry(root), root).collect()

            self.assertEqual("广东体育", collection.candidates[0].canonical_name)
            self.assertEqual("gb18030", collection.results[0].files[0]["encoding"])

    def test_required_empty_source_fails_with_explicit_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inbox").mkdir()
            result = SourceRegistry(self._write_registry(root, required=True), root).collect().results[0]
            self.assertFalse(result.success)
            self.assertEqual("failed", result.status)
            self.assertIn("no_files_matched", result.errors)

    def test_optional_empty_source_is_empty_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inbox").mkdir()
            result = SourceRegistry(self._write_registry(root), root).collect().results[0]
            self.assertFalse(result.success)
            self.assertEqual("empty", result.status)

    def test_unchanged_sha_reuses_parsed_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            playlist = inbox / "one.txt"
            playlist.write_text("广东体育,http://example.test/live\n", encoding="utf-8")
            registry_path = self._write_registry(root)
            first = SourceRegistry(registry_path, root).collect().results[0]
            state = {"sources": {"fixture-inbox": result_state(first)}}
            with patch("sources.file_inbox.parse_playlist_content", side_effect=AssertionError("cache miss")):
                second = SourceRegistry(registry_path, root, state=state).collect().results[0]
            self.assertTrue(second.success)
            self.assertTrue(second.files[0]["from_cache"])
            self.assertEqual(first.candidates, second.candidates)

    def test_corrupt_file_does_not_block_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "bad.m3u").write_bytes(b"\x81")
            (inbox / "good.txt").write_text("广东体育,http://example.test/live\n", encoding="utf-8")
            result = SourceRegistry(self._write_registry(root, required=True), root).collect().results[0]
            self.assertTrue(result.success)
            self.assertEqual("success", result.status)
            self.assertEqual(1, len(result.candidates))
            self.assertEqual(2, len(result.files))

    def test_formal_registry_points_to_pending_sources(self):
        repo_root = Path(__file__).resolve().parents[1]
        registry = SourceRegistry(repo_root / "config" / "sources.json", repo_root)
        inbox_source = next(item for item in registry.config["sources"] if item["type"] == "file_inbox")
        self.assertTrue(inbox_source["required"])
        self.assertTrue(all(path.startswith("config/pending_sources/") for path in inbox_source["paths"]))
        result = FileInboxAdapter(inbox_source, str(repo_root)).collect()
        # Pending sources are intentionally ignored by Git and are supplied to
        # CI from R2.  Validate any locally present files without requiring a
        # developer-specific number of files to be committed to the repo.
        expected_files = [
            path
            for pattern in inbox_source["paths"]
            for path in repo_root.glob(pattern)
        ]
        self.assertEqual(len(expected_files), len(result.files))
        if expected_files:
            self.assertGreater(len(result.candidates), 0)


if __name__ == "__main__":
    unittest.main()
