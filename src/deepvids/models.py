from dataclasses import asdict, dataclass, field
from enum import StrEnum
import math


class Outcome(StrEnum):
    VALID = "valid"
    UNAVAILABLE = "unavailable"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Part:
    key: str
    title: str
    duration_seconds: int | None = None


@dataclass(frozen=True)
class Archive:
    platform: str
    archive_id: str
    title: str
    parts: tuple[Part, ...]
    creator_id: str | None = None
    published_at: str | int | None = None
    views: int | None = None
    access_evidence: str = "metadata_only"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "parts": tuple(Part(**p) for p in value["parts"])})


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    archive: Archive | None = None
    reason: str = ""


@dataclass(frozen=True)
class Policy:
    quality: float = 0.60
    interest: float = 0.25
    engagement: float = 0.05
    recency: float = 0.05
    creator_diversity: float = 0.05
    maximum_duration_seconds: int = 5400
    duration_plateau_seconds: int = 3600
    duration_prior_points: float = 5.0

    def duration_eligible(self, seconds):
        return isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and 0 < seconds <= self.maximum_duration_seconds

    def duration_prior(self, seconds):
        if not self.duration_eligible(seconds):
            return None
        return self.duration_prior_points * min(seconds / self.duration_plateau_seconds, 1.0)


@dataclass
class SearchPage:
    video_ids: list[str] = field(default_factory=list)
    continuation_present: bool = False
    recognized: bool = True

