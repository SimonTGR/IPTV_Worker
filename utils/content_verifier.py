from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from utils.config import config


def is_untrusted_relay(url: str, domains: list[str] | None = None) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    for domain in domains or config.untrusted_relay_domains:
        normalized = domain.lower().lstrip(".")
        if host == normalized or host.endswith("." + normalized):
            return True
    return False


def fingerprint_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    try:
        left_int = int(left, 16)
        right_int = int(right, 16)
    except ValueError:
        return 1.0 if left == right else 0.0
    bits = max(len(left), len(right)) * 4
    return 1.0 - ((left_int ^ right_int).bit_count() / bits if bits else 1.0)


def _frame_average_hash(data: bytes, frame_size: int = 256) -> tuple[str | None, list[str]]:
    frames = [data[index:index + frame_size] for index in range(0, len(data), frame_size)]
    frames = [frame for frame in frames if len(frame) == frame_size]
    if not frames:
        return None, []
    frame_hashes = []
    values = []
    for frame in frames:
        average = sum(frame) / len(frame)
        value = 0
        for byte in frame:
            value = (value << 1) | int(byte >= average)
        values.append(value)
        frame_hashes.append(f"{value:064x}")
    majority = 0
    for bit in range(frame_size):
        ones = sum((value >> bit) & 1 for value in values)
        if ones * 2 >= len(values):
            majority |= 1 << bit
    return f"{majority:064x}", frame_hashes


def _audio_energy_hash(data: bytes, buckets: int = 64) -> str | None:
    if len(data) < buckets * 2:
        return None
    samples = [int.from_bytes(data[index:index + 2], "little", signed=True) for index in range(0, len(data) - 1, 2)]
    bucket_size = max(1, len(samples) // buckets)
    energies = []
    for index in range(buckets):
        bucket = samples[index * bucket_size:(index + 1) * bucket_size]
        energies.append(sum(abs(value) for value in bucket) / len(bucket) if bucket else 0)
    median = sorted(energies)[len(energies) // 2]
    value = 0
    for energy in energies:
        value = (value << 1) | int(energy >= median)
    return f"{value:016x}"


class MediaFingerprinter:
    def __init__(self, timeout: int | None = None, sample_seconds: int = 8, loop_offset_seconds: int = 60):
        self.timeout = timeout or config.content_probe_timeout
        self.sample_seconds = sample_seconds
        self.loop_offset_seconds = loop_offset_seconds

    async def _run(self, args: list[str]) -> bytes:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            return stdout or b""
        except (OSError, asyncio.TimeoutError):
            if process:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            return b""

    def _input_args(self, url: str, headers: dict | None) -> list[str]:
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if headers:
            args += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]
        args += ["-i", url]
        return args

    async def _video_sample(self, url: str, headers: dict | None, offset: int = 0) -> bytes:
        args = self._input_args(url, headers)
        if offset:
            args += ["-ss", str(offset)]
        args += [
            "-t", str(self.sample_seconds), "-an", "-vf", "fps=1/2,scale=16:16:flags=area,format=gray",
            "-f", "rawvideo", "pipe:1",
        ]
        return await self._run(args)

    async def fingerprint_url(self, url: str, headers: dict | None = None) -> dict | None:
        first_video, later_video, audio = await asyncio.gather(
            self._video_sample(url, headers),
            self._video_sample(url, headers, self.loop_offset_seconds),
            self._run(self._input_args(url, headers) + [
                "-t", str(self.sample_seconds), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1",
            ]),
        )
        video_hash, frame_hashes = _frame_average_hash(first_video)
        later_hash, _ = _frame_average_hash(later_video)
        audio_hash = _audio_energy_hash(audio)
        if not video_hash and not audio_hash:
            return None
        fingerprint = video_hash or hashlib.sha256((audio_hash or "").encode()).hexdigest()[:64]
        short_loop = bool(later_hash and video_hash and fingerprint_similarity(video_hash, later_hash) >= 0.98)
        return {
            "fingerprint": fingerprint,
            "video_hash": video_hash,
            "audio_hash": audio_hash,
            "frame_hashes": frame_hashes,
            "short_loop": short_loop,
        }


class KnownBadFingerprints:
    def __init__(self, path: str = "state/known_bad_fingerprints.json"):
        self.path = Path(path)
        self.values = self._load()

    def _load(self) -> list[str]:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return [str(item["fingerprint"] if isinstance(item, dict) else item) for item in data.get("fingerprints", [])]
        except (OSError, ValueError, TypeError):
            return []

    def matches(self, fingerprint: str, threshold: float = 0.92) -> bool:
        return any(fingerprint_similarity(fingerprint, known) >= threshold for known in self.values)

    def add(self, fingerprint: str, label: str = "confirmed wrong content") -> None:
        if fingerprint in self.values:
            return
        self.values.append(fingerprint)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        target_data = {"schema_version": 1, "fingerprints": [
            {"fingerprint": value, "label": label if value == fingerprint else "known bad"}
            for value in self.values
        ]}
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as file:
                json.dump(target_data, file, ensure_ascii=False, indent=2)
                file.write("\n")
                temp_path = file.name
            os.replace(temp_path, self.path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)


class ContentVerifier:
    def __init__(self, fingerprinter=None, known_bad=None, similarity_threshold: float = 0.92):
        self.fingerprinter = fingerprinter or MediaFingerprinter()
        self.known_bad = known_bad or KnownBadFingerprints()
        self.similarity_threshold = similarity_threshold
        self.semaphore = asyncio.Semaphore(config.content_probe_concurrency)

    @staticmethod
    def _preliminary_key(item: dict):
        return (
            not bool(item.get("playable", item.get("delay", -1) != -1)),
            -(item.get("success_ratio") or 0),
            -(item.get("download_speed_mbps", item.get("speed")) or 0),
            item.get("url") or "",
        )

    def _select(self, grouped_results: dict) -> list[tuple[str, dict]]:
        selected = []
        host_counts = Counter(
            item.get("host")
            for channel_obj in grouped_results.values()
            for items in channel_obj.values()
            for item in items
            if item.get("host")
        )
        seen_urls = set()
        for channel_obj in grouped_results.values():
            for channel_name, items in channel_obj.items():
                ranked = sorted(items, key=self._preliminary_key)
                candidates = ranked[:3] + [
                    item for item in ranked
                    if is_untrusted_relay(item.get("url", "")) or host_counts[item.get("host")] >= 3
                ]
                for item in candidates:
                    url = item.get("url")
                    if not url or url in seen_urls or not item.get("playable", item.get("delay", -1) != -1):
                        continue
                    seen_urls.add(url)
                    selected.append((channel_name, item))
        return selected

    async def _fingerprint(self, item: dict):
        async with self.semaphore:
            return await self.fingerprinter.fingerprint_url(item["url"], item.get("headers"))

    async def verify_grouped_results(self, grouped_results: dict) -> dict:
        if not config.open_content_verification:
            return grouped_results
        selected = self._select(grouped_results)
        fingerprints = await asyncio.gather(*(self._fingerprint(item) for _, item in selected), return_exceptions=True)
        verified = []
        for (channel_name, item), result in zip(selected, fingerprints):
            untrusted = is_untrusted_relay(item.get("url", ""))
            item["untrusted_relay"] = untrusted
            if isinstance(result, Exception) or not result:
                item["content_verified"] = False if untrusted else None
                if untrusted and not item.get("failure_reason"):
                    item["failure_reason"] = "content_unverified"
                continue
            fingerprint = result["fingerprint"]
            item["content_fingerprint"] = fingerprint
            item["content_verified"] = True
            if self.known_bad.matches(fingerprint):
                item.update(playable=False, content_verified=False, failure_reason="wrong_content")
            elif untrusted and result.get("short_loop"):
                item.update(playable=False, content_verified=False, failure_reason="wrong_content")
            else:
                verified.append((channel_name, item, fingerprint))

        parent = list(range(len(verified)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for left in range(len(verified)):
            for right in range(left + 1, len(verified)):
                if fingerprint_similarity(verified[left][2], verified[right][2]) >= self.similarity_threshold:
                    parent[find(right)] = find(left)

        clusters = defaultdict(list)
        for index, entry in enumerate(verified):
            clusters[find(index)].append(entry)
        for cluster in clusters.values():
            if len({channel_name for channel_name, _, _ in cluster}) >= 3:
                for _, item, _ in cluster:
                    item.update(playable=False, content_verified=False, failure_reason="placeholder_fingerprint")
        return grouped_results
