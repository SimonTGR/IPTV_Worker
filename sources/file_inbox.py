from __future__ import annotations

import glob
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from sources.base import Candidate, SourceAdapter, SourceResult
from sources.normalize import decode_playlist, parse_playlist_content


class FileInboxAdapter(SourceAdapter):
    ALLOWED_EXTENSIONS = {".m3u", ".m3u8", ".txt"}

    def collect(self, force_refresh: bool = False) -> SourceResult:
        result = SourceResult(source_id=self.source_id, source_type=self.source_type)
        max_file_size = int(self.source.get("max_file_size", 10 * 1024 * 1024))
        discovered_at = datetime.now(timezone.utc).isoformat()
        matched_paths: set[str] = set()
        previous_cache = self.state.get("state_data", {}).get("file_cache", {})
        next_cache = {}

        for pattern in self.source.get("paths", []):
            expanded = pattern if os.path.isabs(pattern) else os.path.join(self.base_dir, pattern)
            for match in glob.glob(expanded, recursive=True):
                path = Path(match)
                if not path.is_file() or path.suffix.lower() not in self.ALLOWED_EXTENSIONS:
                    continue
                matched_paths.add(str(path.resolve()))

        for filename in sorted(matched_paths):
            path = Path(filename)
            file_report = {"path": os.path.relpath(path, self.base_dir).replace("\\", "/")}
            try:
                data = path.read_bytes()
                if not data:
                    raise ValueError("empty file")
                if len(data) > max_file_size:
                    raise ValueError(f"file exceeds {max_file_size} byte limit")
                content, encoding = decode_playlist(data)
                sha256 = hashlib.sha256(data).hexdigest()
                cached = previous_cache.get(file_report["path"], {})
                if cached.get("sha256") == sha256:
                    candidates = [Candidate(**item) for item in cached.get("candidates", [])]
                    from_cache = True
                else:
                    candidates = parse_playlist_content(
                        content,
                        source_id=self.source_id,
                        source_type=self.source_type,
                        source_priority=self.priority,
                        source_path=file_report["path"],
                        discovered_at=discovered_at,
                    )
                    from_cache = False
                file_report.update(
                    {
                        "sha256": sha256,
                        "size": len(data),
                        "encoding": encoding,
                        "candidate_count": len(candidates),
                        "success": True,
                        "from_cache": from_cache,
                    }
                )
                result.candidates.extend(candidates)
                next_cache[file_report["path"]] = {
                    "sha256": sha256,
                    "candidates": [candidate.__dict__ for candidate in candidates],
                }
            except Exception as exc:
                file_report.update({"success": False, "error": str(exc)})
                result.errors.append(f"{file_report['path']}: {exc}")
            result.files.append(file_report)

        result.state_data = {"file_cache": next_cache}
        required = bool(self.source.get("required", False))
        if not matched_paths:
            result.status = "failed" if required else "empty"
            result.success = False
            result.errors.append("no_files_matched")
        elif not result.candidates:
            had_processing_errors = bool(result.errors)
            reason = "no_candidates_parsed"
            if reason not in result.errors:
                result.errors.append(reason)
            result.status = "failed" if required or had_processing_errors else "empty"
            result.success = False
        else:
            result.status = "success"
            result.success = True
        return result
