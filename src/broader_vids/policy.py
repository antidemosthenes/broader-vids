from dataclasses import dataclass
import math


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

    @property
    def weights(self):
        return {
            "quality": self.quality,
            "interest": self.interest,
            "engagement": self.engagement,
            "recency": self.recency,
            "diversity": self.creator_diversity,
        }

    def duration_eligible(self, seconds):
        return (
            isinstance(seconds, (int, float))
            and not isinstance(seconds, bool)
            and math.isfinite(seconds)
            and 0 < seconds <= self.maximum_duration_seconds
        )

    def duration_prior(self, seconds):
        if not self.duration_eligible(seconds):
            return None
        return self.duration_prior_points * min(
            seconds / self.duration_plateau_seconds, 1.0
        )
