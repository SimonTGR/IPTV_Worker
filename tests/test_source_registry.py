import json
import tempfile
import unittest
from pathlib import Path

from sources.base import SourceResult
from sources.registry import SourceRegistry


class SourceRegistryTests(unittest.TestCase):
    def test_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sources.json")
            path.write_text(json.dumps({
                "schema_version": 1,
                "sources": [
                    {"id": "same", "type": "file_inbox", "paths": []},
                    {"id": "same", "type": "file_inbox", "paths": []},
                ],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate source id"):
                SourceRegistry(path, directory)

    def test_rejects_unknown_enabled_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sources.json")
            path.write_text(json.dumps({
                "schema_version": 1,
                "sources": [{"id": "future", "type": "not_implemented"}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported enabled source type"):
                SourceRegistry(path, directory)

    def test_report_redacts_sensitive_urls_recursively(self):
        report = SourceResult(
            "test", "http_playlist", success=False, status="failed",
            errors=["failed https://example.test/live?token=secret&quality=hd"],
            metadata={"nested": {"entry": "https://example.test/live?tk=secret"}},
        ).to_report()
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("secret", encoded)
        self.assertIn("quality=hd", encoded)


if __name__ == "__main__":
    unittest.main()
