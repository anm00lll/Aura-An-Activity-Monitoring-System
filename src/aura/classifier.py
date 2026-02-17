from __future__ import annotations

"""
Distraction classifier for AURA.

Goals:
- Binary decision: focused vs distracted
- Also label a specific distraction category with high confidence where possible:
  social | entertainment | youtube_shorts | news | gaming | communication_personal | other
- Context awareness for browsers, YouTube (educational vs shorts), communication apps
- Time-based heuristics: brief checks (<15s) vs longer distractions; repeated quick-check patterns
- Configurable exceptions: whitelists and temporary break mode

Typical integration with ActivityTracker:

    from aura.activity_tracker import ActivityTracker
    from aura.classifier import DistractionClassifier

    clf = DistractionClassifier()

    def on_evt(evt):
        if evt.type == "focus_change":
            title = str(evt.payload.get("title") or "")
            app = str(evt.payload.get("app") or "")
            res = clf.observe(window_title=title, app_name=app, now_ms=evt.ts_ms)
            # res.is_distracted -> drive UI/notifications
            # res.category, res.confidence, res.reasons for details

    tr = ActivityTracker(allowed_keywords=[...])
    tr.add_listener(on_evt)
    tr.start()
"""

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------- Config and results ----------------------------


@dataclass
class ClassifierConfig:
    # Domain/app lists (lowercase)
    social_domains: Set[str] = field(
        default_factory=lambda: {
            "instagram.com",
            "facebook.com",
            "fb.com",
            "twitter.com",
            "x.com",
            "reddit.com",
            "snapchat.com",
            "threads.net",
        }
    )
    entertainment_domains: Set[str] = field(
        default_factory=lambda: {
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "netflix.com",
            "primevideo.com",
            "hotstar.com",
            "disneyplus.com",
            "disneyplus.hotstar.com",
            "twitch.tv",
            "spotify.com",
            "soundcloud.com",
        }
    )
    news_domains: Set[str] = field(
        default_factory=lambda: {
            "cnn.com",
            "bbc.com",
            "nytimes.com",
            "theguardian.com",
            "indiatimes.com",
            "hindustantimes.com",
            "indianexpress.com",
            "washingtonpost.com",
            "wsj.com",
            "reuters.com",
        }
    )
    communication_apps: Set[str] = field(
        default_factory=lambda: {
            "slack.exe",
            "ms-teams.exe",
            "teams.exe",
            "outlook.exe",
            "zoom.exe",
            "discord.exe",
            "telegram.exe",
            "whatsapp.exe",
            "skype.exe",
        }
    )
    communication_domains: Set[str] = field(
        default_factory=lambda: {
            "slack.com",
            "teams.microsoft.com",
            "outlook.office.com",
            "outlook.live.com",
            "mail.google.com",
            "discord.com",
            "web.telegram.org",
            "web.whatsapp.com",
            "zoom.us",
        }
    )
    browser_procs: Set[str] = field(
        default_factory=lambda: {
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "brave.exe",
            "opera.exe",
            "opera_gx.exe",
            "vivaldi.exe",
        }
    )
    gaming_apps: Set[str] = field(
        default_factory=lambda: {
            "steam.exe",
            "epicgameslauncher.exe",
            "valorant.exe",
            "cs2.exe",
            "csgo.exe",
            "minecraft.exe",
        }
    )

    # Heuristic keywords
    youtube_edu_keywords: Set[str] = field(
        default_factory=lambda: {
            "tutorial",
            "course",
            "lecture",
            "how to",
            "explained",
            "crash course",
            "walkthrough",
            "guide",
            "documentation",
            "khan academy",
            "freecodecamp",
            "mit",
            "stanford",
            "coursera",
            "edx",
        }
    )
    work_keywords: Set[str] = field(
        default_factory=lambda: {
            "standup",
            "sprint",
            "retro",
            "review",
            "planning",
            "design",
            "spec",
            "meeting",
            "client",
            "jira",
            "asana",
            "trello",
            "github",
            "gitlab",
            "bitbucket",
            "ticket",
            "project",
        }
    )
    work_domains: Set[str] = field(default_factory=lambda: set())  # e.g. {"yourcompany.com"}

    # Exceptions
    whitelist_domains: Set[str] = field(default_factory=lambda: set())
    whitelist_apps: Set[str] = field(default_factory=lambda: set())

    # Time heuristics (seconds)
    brief_check_s: int = 15
    repeated_visit_window_s: int = 10 * 60
    repeated_visit_count_threshold: int = 3


@dataclass
class ClassificationResult:
    is_distracted: bool
    category: str  # e.g., "social" | "entertainment" | "youtube_shorts" | "news" | "gaming" | "communication_personal" | "other" | "work" | "break"
    confidence: float
    reasons: List[str]
    matched_app: Optional[str] = None
    matched_domain: Optional[str] = None
    subcategory: Optional[str] = None  # e.g., "educational_video"
    duration_s: float = 0.0  # time in current category since last switch


# ---------------------------- Classifier core ----------------------------


class DistractionClassifier:
    def __init__(self, config: Optional[ClassifierConfig] = None) -> None:
        self.cfg = config or ClassifierConfig()
        self._break_until_ms: int = 0

        # State for time heuristics
        self._current_category: Optional[str] = None
        self._current_start_ms: Optional[int] = None
        # history of category visits (ms timestamps per category)
        self._history: Dict[str, List[int]] = {}

    # ---------- Controls ----------
    def set_break(self, seconds: int) -> None:
        self._break_until_ms = int(time.time() * 1000) + max(0, seconds) * 1000

    def set_break_active(self, active: bool, duration_s: Optional[int] = None) -> None:
        if active:
            self.set_break(duration_s or self.cfg.repeated_visit_window_s)
        else:
            self._break_until_ms = 0

    def add_whitelist_domain(self, domain: str) -> None:
        self.cfg.whitelist_domains.add(domain.lower())

    def add_whitelist_app(self, app: str) -> None:
        self.cfg.whitelist_apps.add(app.lower())

    # ---------- Public API ----------
    def observe(
        self,
        window_title: str,
        app_name: str,
        now_ms: Optional[int] = None,
        url: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify the current context and update internal timers/history.

        Inputs:
          - window_title: Active window title (case-insensitive matching)
          - app_name: Process name like 'chrome.exe', 'slack.exe'
          - now_ms: Millisecond timestamp (defaults to time.time())
          - url: Optional URL if you collect it elsewhere (browser extension/integration).
        """
        ts = now_ms or int(time.time() * 1000)

        if self._is_on_break(ts):
            return self._result(
                distracted=False,
                category="break",
                confidence=0.99,
                reasons=["break_active"],
                app=app_name,
            )

        title_l = (window_title or "").lower()
        app_l = (app_name or "").lower()
        domain = self._extract_domain(url) if url else self._match_known_domain_in_title(title_l)

        if self._is_whitelisted(app_l, domain):
            return self._result(
                distracted=False,
                category="work",
                confidence=0.98,
                reasons=["whitelist"],
                app=app_name,
                domain=domain,
            )

        # Core category detection
        category, subcat, base_conf, reasons = self._detect_category(title_l, app_l, domain)

        # Time heuristics
        duration_s = self._update_duration(category, ts)
        repeated = self._is_repeated(category, ts)

        # Decide distraction boolean
        distracted = self._category_is_distractive(category, subcat)
        if distracted and duration_s < self.cfg.brief_check_s and not repeated:
            # Treat brief first checks as not distracted
            distracted = False
            reasons.append(f"brief_check<{self.cfg.brief_check_s}s")
            base_conf = max(0.6, base_conf - 0.2)
        if self._category_is_distractive(category, subcat) and repeated:
            reasons.append("repeated_pattern")
            base_conf = min(0.99, base_conf + 0.05)

        return ClassificationResult(
            is_distracted=distracted,
            category=category,
            confidence=round(base_conf, 2),
            reasons=reasons,
            matched_app=app_name or None,
            matched_domain=domain,
            subcategory=subcat,
            duration_s=duration_s,
        )

    # ---------- Internals ----------
    def _is_on_break(self, now_ms: int) -> bool:
        return bool(self._break_until_ms) and now_ms < self._break_until_ms

    def _result(
        self,
        distracted: bool,
        category: str,
        confidence: float,
        reasons: List[str],
        app: Optional[str] = None,
        domain: Optional[str] = None,
        subcat: Optional[str] = None,
        duration_s: float = 0.0,
    ) -> ClassificationResult:
        return ClassificationResult(
            is_distracted=distracted,
            category=category,
            confidence=confidence,
            reasons=reasons,
            matched_app=app or None,
            matched_domain=domain,
            subcategory=subcat,
            duration_s=duration_s,
        )

    def _extract_domain(self, url: str) -> Optional[str]:
        # Very small parser; avoid importing urlparse
        try:
            m = re.match(r"^[a-zA-Z]+://([^/]+)/?", url)
            if m:
                return m.group(1).lower()
        except Exception:
            return None
        return None

    def _match_known_domain_in_title(self, title_l: str) -> Optional[str]:
        # Fast substring scan for known domains across configured sets
        all_domains = self.cfg.social_domains | self.cfg.entertainment_domains | self.cfg.news_domains | self.cfg.communication_domains
        for d in all_domains:
            if d in title_l:
                return d
        # Heuristic: find something that looks like a domain
        m = re.search(r"([a-z0-9.-]+\.(com|net|org|io|ai|edu|gov|tv|in|co))", title_l)
        if m:
            return m.group(1)
        return None

    def _is_whitelisted(self, app_l: str, domain: Optional[str]) -> bool:
        if app_l in self.cfg.whitelist_apps:
            return True
        if domain and domain in self.cfg.whitelist_domains:
            return True
        return False

    def _detect_category(
        self, title_l: str, app_l: str, domain: Optional[str]
    ) -> Tuple[str, Optional[str], float, List[str]]:
        reasons: List[str] = []

        # Explicit apps
        if app_l in self.cfg.gaming_apps:
            return "gaming", None, 0.95, ["gaming_app"]

        # Communication
        if app_l in self.cfg.communication_apps or (domain and domain in self.cfg.communication_domains):
            if self._looks_work_related(title_l, domain):
                return "work", "communication_work", 0.85, ["communication_work"]
            else:
                return "communication_personal", None, 0.9, ["communication_personal"]

        # Browser-based categories via domain
        is_browser = app_l in self.cfg.browser_procs

        # YouTube special-casing
        if (domain and domain.endswith("youtube.com")) or (is_browser and "youtube" in title_l):
            sub, conf_bump, reason = self._youtube_subclass(title_l)
            reasons.append(reason)
            if sub == "youtube_shorts":
                return "youtube_shorts", sub, 0.95 + conf_bump, reasons
            elif sub == "educational_video":
                return "work", sub, 0.85 + conf_bump, reasons
            else:
                return "entertainment", sub, 0.9 + conf_bump, reasons

        if domain and domain in self.cfg.social_domains:
            return "social", None, 0.95, ["social_domain"]
        if domain and domain in self.cfg.entertainment_domains:
            return "entertainment", None, 0.9, ["entertainment_domain"]
        if domain and domain in self.cfg.news_domains:
            return "news", None, 0.9, ["news_domain"]

        # Title-based hints as last resort
        if any(k in title_l for k in ["facebook", "instagram", "twitter", "tiktok", "reddit", "netflix", "prime video", "disney+", "hotstar"]):
            return "entertainment", None, 0.75, ["title_hint"]

        # Default fallbacks
        if is_browser:
            # Unknown browsing; consider as work unless contradicted later
            return "work", "work_browsing", 0.7, ["browser_unknown"]

        return "other", None, 0.6, ["uncategorized"]

    def _youtube_subclass(self, title_l: str) -> Tuple[str, float, str]:
        # Returns (subcat, conf_bump, reason)
        if "shorts" in title_l or "#shorts" in title_l:
            return "youtube_shorts", 0.02, "youtube_shorts"
        if any(k in title_l for k in self.cfg.youtube_edu_keywords):
            return "educational_video", 0.03, "youtube_educational"
        # Music/video entertainment catch-all
        return "video", 0.0, "youtube_video"

    def _looks_work_related(self, title_l: str, domain: Optional[str]) -> bool:
        if domain and any(wd in domain for wd in self.cfg.work_domains):
            return True
        if any(k in title_l for k in self.cfg.work_keywords):
            return True
        return False

    def _category_is_distractive(self, category: str, subcat: Optional[str]) -> bool:
        if category in {"social", "entertainment", "news", "gaming", "communication_personal", "youtube_shorts"}:
            return True
        if category == "other":
            return False
        # "work" categories and "break" are not distractive
        return False

    def _update_duration(self, category: str, ts_ms: int) -> float:
        # Track current category dwell time
        if self._current_category != category:
            # finalize old category visit
            if self._current_category and self._current_start_ms is not None:
                self._record_visit(self._current_category, ts_ms)
            self._current_category = category
            self._current_start_ms = ts_ms
            return 0.0
        if self._current_start_ms is None:
            self._current_start_ms = ts_ms
            return 0.0
        return max(0.0, (ts_ms - self._current_start_ms) / 1000.0)

    def _record_visit(self, category: str, ts_ms: int) -> None:
        if category not in self._history:
            self._history[category] = []
        self._history[category].append(ts_ms)
        # Trim old entries
        cutoff = ts_ms - self.cfg.repeated_visit_window_s * 1000
        self._history[category] = [t for t in self._history[category] if t >= cutoff]

    def _is_repeated(self, category: str, ts_ms: int) -> bool:
        # Count visits in window; include current start if switching just happened
        visits = self._history.get(category, [])
        cutoff = ts_ms - self.cfg.repeated_visit_window_s * 1000
        count = len([t for t in visits if t >= cutoff])
        return count >= self.cfg.repeated_visit_count_threshold


__all__ = [
    "ClassifierConfig",
    "ClassificationResult",
    "DistractionClassifier",
]
