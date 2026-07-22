from typing import TypedDict, Literal, Union, NotRequired

OriginType = Literal["hls", "local", "whitelist", "subscribe"]
IPvType = Literal["ipv4", "ipv6", None]


class ChannelData(TypedDict):
    """
    Channel data types, including url, date, resolution, origin and ipv_type
    """
    id: int
    url: str
    host: str
    date: NotRequired[str | None]
    resolution: NotRequired[str | None]
    video_codec: NotRequired[str | None]
    audio_codec: NotRequired[str | None]
    fps: NotRequired[float | None]
    origin: OriginType
    ipv_type: IPvType
    location: NotRequired[str | None]
    isp: NotRequired[str | None]
    headers: NotRequired[dict[str, str] | None]
    catchup: NotRequired[dict[str, str] | None]
    tvg_logo: NotRequired[str | None]
    extra_info: NotRequired[str]
    supply: NotRequired[bool]
    source_id: NotRequired[str]
    source_type: NotRequired[str]
    source_priority: NotRequired[int]
    discovered_at: NotRequired[str]
    source_path: NotRequired[str | None]
    dynamic_base: NotRequired[bool]
    playable: NotRequired[bool]
    download_speed_mbps: NotRequired[float | None]
    bitrate_kbps: NotRequired[float | None]
    bitrate_estimated: NotRequired[bool]
    delay_ms: NotRequired[int | None]
    success_ratio: NotRequired[float]
    consecutive_failures: NotRequired[int]
    content_verified: NotRequired[bool | None]
    content_fingerprint: NotRequired[str | None]
    failure_reason: NotRequired[str | None]
    untrusted_relay: NotRequired[bool]


CategoryChannelData = dict[str, dict[str, list[ChannelData]]]


class TestResult(TypedDict):
    """
    Test result types, including speed, delay, resolution
    """
    speed: int | float | None
    delay: int | float | None
    playable: NotRequired[bool]
    download_speed_mbps: NotRequired[float | None]
    bitrate_kbps: NotRequired[float | None]
    bitrate_estimated: NotRequired[bool]
    delay_ms: NotRequired[int | None]
    resolution: NotRequired[str | None]
    video_codec: NotRequired[str | None]
    audio_codec: NotRequired[str | None]
    fps: NotRequired[float | None]
    success_ratio: NotRequired[float]
    consecutive_failures: NotRequired[int]
    content_verified: NotRequired[bool | None]
    content_fingerprint: NotRequired[str | None]
    failure_reason: NotRequired[str | None]


TestResultCacheData = dict[str, list[TestResult]]

ChannelTestResult = Union[ChannelData, TestResult]

WhitelistMaps = tuple[dict[str, list[str]], dict[str, list[str]]]
