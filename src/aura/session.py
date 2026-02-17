from dataclasses import dataclass, field
from time import time
from typing import Dict, Set


def _new_str_set() -> Set[str]:
    return set()


@dataclass
class SessionStats:
    focused_seconds: int = 0
    unfocused_seconds: int = 0

    def reset(self) -> None:
        self.focused_seconds = 0
        self.unfocused_seconds = 0

    @property
    def total_seconds(self) -> int:
        return self.focused_seconds + self.unfocused_seconds


@dataclass
class Session:
    allowed_apps: Set[str] = field(default_factory=_new_str_set)
    start_ts: float = field(default_factory=time)
    last_tick: float = field(default_factory=time)
    stats: SessionStats = field(default_factory=SessionStats)

    def tick(self, is_focused: bool, now: float | None = None) -> None:
        """Advance session time by difference since last_tick based on focus state."""
        now = now or time()
        delta = max(0, int(now - self.last_tick))
        if delta:
            if is_focused:
                self.stats.focused_seconds += delta
            else:
                self.stats.unfocused_seconds += delta
            self.last_tick = now

    def to_dict(self) -> Dict[str, int]:
        return {
            "focused_seconds": self.stats.focused_seconds,
            "unfocused_seconds": self.stats.unfocused_seconds,
            "total_seconds": self.stats.total_seconds,
        }

    def reset(self) -> None:
        self.start_ts = time()
        self.last_tick = self.start_ts
        self.stats.reset()
