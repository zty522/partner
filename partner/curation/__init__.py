"""Daily Research Paper Curation Loop.

Inspired by PaperFlow (Shanghai AI Lab, 2026), adapted for bioinformatics
research curation as a Partner module.

The loop runs daily:
  1. Profiling  — build/update structured researcher profile from cold-start
                  evidence and ongoing feedback
  2. Recommending — rank today's paper pool with multi-signal scoring
  3. Adapting    — update profile based on implicit feedback (clicks, skips,
                  deep reads)
  4. Drift Tracking — detect research interest shifts using a state machine
                       (Stable → Observing → Shifting → Recovered)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Drift State Machine
# ---------------------------------------------------------------------------

class DriftState(Enum):
    STABLE = "stable"        # Recent behavior matches long-term profile
    OBSERVING = "observing"  # New direction emerging, evidence insufficient
    SHIFTING = "shifting"    # Sustained evidence, new topic weight rising
    RECOVERED = "recovered"  # Shift confirmed, rebalancing old/new interests


@dataclass
class ResearchProfile:
    """Structured academic profile for a researcher."""

    # Core research directions
    directions: list[str] = field(default_factory=list)
    # Topic weights (topic → weight 0–1)
    topic_weights: dict[str, float] = field(default_factory=dict)
    # Author/institution priors
    favorite_authors: list[str] = field(default_factory=list)
    favorite_institutions: list[str] = field(default_factory=list)
    # Methodological preferences
    preferred_methods: list[str] = field(default_factory=list)
    preferred_paper_types: list[str] = field(default_factory=list)  # e.g. ["method", "benchmark", "review"]
    # Must-read rules (keywords that force inclusion)
    must_read_keywords: list[str] = field(default_factory=list)
    # Reading behavior
    reading_behavior: dict[str, Any] = field(default_factory=dict)
    # Drift tracking
    drift_state: DriftState = DriftState.STABLE
    drift_direction: str = ""  # Emerging topic direction
    drift_evidence_count: int = 0
    # Metadata
    last_updated: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "directions": self.directions,
            "topic_weights": self.topic_weights,
            "favorite_authors": self.favorite_authors,
            "favorite_institutions": self.favorite_institutions,
            "preferred_methods": self.preferred_methods,
            "preferred_paper_types": self.preferred_paper_types,
            "must_read_keywords": self.must_read_keywords,
            "reading_behavior": self.reading_behavior,
            "drift_state": self.drift_state.value,
            "drift_direction": self.drift_direction,
            "drift_evidence_count": self.drift_evidence_count,
            "last_updated": self.last_updated,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchProfile:
        return cls(
            directions=data.get("directions", []),
            topic_weights=data.get("topic_weights", {}),
            favorite_authors=data.get("favorite_authors", []),
            favorite_institutions=data.get("favorite_institutions", []),
            preferred_methods=data.get("preferred_methods", []),
            preferred_paper_types=data.get("preferred_paper_types", []),
            must_read_keywords=data.get("must_read_keywords", []),
            reading_behavior=data.get("reading_behavior", {}),
            drift_state=DriftState(data.get("drift_state", "stable")),
            drift_direction=data.get("drift_direction", ""),
            drift_evidence_count=data.get("drift_evidence_count", 0),
            last_updated=data.get("last_updated", ""),
            created_at=data.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------

class PaperProfiler:
    """Build and maintain a structured researcher profile.

    Cold-start from explicit inputs (research description, representative papers),
    then continuously update from implicit feedback.
    """

    def __init__(self, profile_path: str = ""):
        self._profile_path = profile_path
        self._profile: ResearchProfile | None = None

    def load(self) -> ResearchProfile:
        """Load existing profile or create new."""
        if self._profile:
            return self._profile

        if self._profile_path and os.path.exists(self._profile_path):
            try:
                with open(self._profile_path, encoding="utf-8") as f:
                    data = json.load(f)
                self._profile = ResearchProfile.from_dict(data)
                return self._profile
            except Exception as e:
                logger.warning("Failed to load profile: %s", e)

        self._profile = ResearchProfile(
            created_at=datetime.now().isoformat(),
            last_updated=datetime.now().isoformat(),
        )
        return self._profile

    def save(self) -> None:
        """Persist profile to disk."""
        if not self._profile or not self._profile_path:
            return
        self._profile.last_updated = datetime.now().isoformat()
        os.makedirs(os.path.dirname(self._profile_path), exist_ok=True)
        with open(self._profile_path, "w", encoding="utf-8") as f:
            json.dump(self._profile.to_dict(), f, ensure_ascii=False, indent=2)

    def init_from_description(
        self,
        description: str,
        methods: list[str] | None = None,
        keywords: list[str] | None = None,
        authors: list[str] | None = None,
    ) -> ResearchProfile:
        """Initialize profile from a natural-language research description."""
        profile = self.load()
        profile.directions = [description[:200]]
        if methods:
            profile.preferred_methods = methods
        if keywords:
            profile.must_read_keywords = keywords
            for kw in keywords:
                profile.topic_weights[kw] = 0.5
        if authors:
            profile.favorite_authors = authors
        self._profile = profile
        self.save()
        return profile

    def init_from_papers(self, paper_titles: list[str], paper_abstracts: list[str]) -> ResearchProfile:
        """Extract profile from representative papers."""
        profile = self.load()
        # Simple keyword extraction from titles and abstracts
        all_text = " ".join(paper_titles + paper_abstracts).lower()
        # Extract frequent bigrams as topic signals
        words = all_text.split()
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        from collections import Counter
        freq = Counter(bigrams)
        # Top 10 bigrams as initial topics
        for bigram, _ in freq.most_common(10):
            if len(bigram) > 5:  # Filter short pairs
                profile.topic_weights[bigram] = 0.3
        profile.directions = paper_titles[:3]
        self._profile = profile
        self.save()
        return profile

    @property
    def profile(self) -> ResearchProfile:
        return self.load()


# ---------------------------------------------------------------------------
# Recommending
# ---------------------------------------------------------------------------

@dataclass
class PaperCandidate:
    """A paper to be scored for recommendation."""

    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    arxiv_id: str = ""
    published_date: str = ""
    source: str = ""  # "arxiv", "biorxiv", "pubmed"
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "abstract": self.abstract[:500],
            "authors": self.authors,
            "institutions": self.institutions,
            "arxiv_id": self.arxiv_id,
            "published_date": self.published_date,
            "source": self.source,
            "url": self.url,
        }


class DailyRecommender:
    """Multi-signal paper recommendation for daily curation."""

    TOP_N = 20

    def __init__(self, profiler: PaperProfiler):
        self._profiler = profiler

    def rank(
        self,
        candidates: list[PaperCandidate],
        top_n: int | None = None,
    ) -> list[tuple[PaperCandidate, float, str]]:
        """Score and rank paper candidates.

        Returns list of (paper, score, tier) sorted by score descending.
        Tiers: must_read, high_relevant, maybe_interested, edge_relevant.
        """
        if top_n is None:
            top_n = self.TOP_N

        profile = self._profiler.profile
        scored: list[tuple[PaperCandidate, float, str]] = []

        for paper in candidates:
            score = 0.0
            signals: list[str] = []

            # Signal 1: Semantic match — topic weight overlap
            topic_score = self._topic_match(paper, profile)
            score += topic_score * 0.40
            if topic_score > 0.5:
                signals.append("topic_match")

            # Signal 2: Author/institution prior
            prior_score = self._author_prior(paper, profile)
            score += prior_score * 0.20
            if prior_score > 0:
                signals.append("author_prior")

            # Signal 3: Method preference match
            method_score = self._method_match(paper, profile)
            score += method_score * 0.15

            # Signal 4: Must-read keyword hit (bonus, doesn't override relevance)
            must_read = self._must_read_hit(paper, profile)
            if must_read:
                score += 0.10
                signals.append("must_read")

            # Signal 5: Drift alignment (boost emerging topics)
            drift_score = self._drift_alignment(paper, profile)
            score += drift_score * 0.10

            # Signal 6: Recency boost (newer papers slightly preferred)
            recency = self._recency_score(paper)
            score += recency * 0.05

            # Determine tier
            if must_read:
                tier = "must_read"
            elif score >= 0.6:
                tier = "high_relevant"
            elif score >= 0.35:
                tier = "maybe_interested"
            else:
                tier = "edge_relevant"

            scored.append((paper, min(score, 1.0), tier))

        # Sort by score descending
        scored.sort(key=lambda x: -x[1])
        return scored[:top_n]

    # ------------------------------------------------------------------
    # Scoring sub-functions
    # ------------------------------------------------------------------

    def _topic_match(self, paper: PaperCandidate, profile: ResearchProfile) -> float:
        """Weighted topic overlap between paper and profile."""
        text = (paper.title + " " + paper.abstract).lower()
        total_weight = 0.0
        matched_weight = 0.0
        for topic, weight in profile.topic_weights.items():
            total_weight += weight
            if topic.lower() in text:
                matched_weight += weight
        if total_weight == 0:
            return 0.0
        return matched_weight / total_weight

    def _author_prior(self, paper: PaperCandidate, profile: ResearchProfile) -> float:
        """Boost papers by favorite authors or institutions."""
        score = 0.0
        for author in paper.authors:
            if any(fa.lower() in author.lower() for fa in profile.favorite_authors):
                score += 0.5
        for inst in paper.institutions:
            if any(fi.lower() in inst.lower() for fi in profile.favorite_institutions):
                score += 0.3
        return min(score, 1.0)

    def _method_match(self, paper: PaperCandidate, profile: ResearchProfile) -> float:
        """Check if paper uses preferred methods."""
        if not profile.preferred_methods:
            return 0.0
        text = (paper.title + " " + paper.abstract).lower()
        hits = sum(1 for m in profile.preferred_methods if m.lower() in text)
        return min(hits / len(profile.preferred_methods), 1.0)

    def _must_read_hit(self, paper: PaperCandidate, profile: ResearchProfile) -> bool:
        """Check if paper matches must-read keywords."""
        if not profile.must_read_keywords:
            return False
        text = (paper.title + " " + paper.abstract).lower()
        return any(kw.lower() in text for kw in profile.must_read_keywords)

    def _drift_alignment(self, paper: PaperCandidate, profile: ResearchProfile) -> float:
        """Boost papers aligned with current drift direction."""
        if profile.drift_state in (DriftState.STABLE, DriftState.RECOVERED):
            return 0.0
        if not profile.drift_direction:
            return 0.0
        text = (paper.title + " " + paper.abstract).lower()
        if profile.drift_direction.lower() in text:
            return 0.5 if profile.drift_state == DriftState.OBSERVING else 0.8
        return 0.0

    def _recency_score(self, paper: PaperCandidate) -> float:
        """Slight boost for very recent papers."""
        if not paper.published_date:
            return 0.0
        try:
            dt = datetime.fromisoformat(paper.published_date[:10])
            age_days = (datetime.now() - dt).days
            if age_days <= 1:
                return 1.0
            elif age_days <= 7:
                return 0.5
            elif age_days <= 30:
                return 0.2
        except (ValueError, TypeError):
            pass
        return 0.0


# ---------------------------------------------------------------------------
# Adapting (Feedback Learning)
# ---------------------------------------------------------------------------

class FeedbackLearner:
    """Update the profile based on implicit reading feedback.

    Feedback types:
      - Selected / clicked:  strong positive signal → boost topic weight
      - Skipped:             weak negative signal  → slight topic decay
      - Deep read / saved:   medium positive       → boost topic + author
      - Report style feedback: isolated channel    → only affects reading behavior
    """

    WEIGHT_BOOST_SELECTED = 0.15
    WEIGHT_BOOST_DEEP_READ = 0.08
    WEIGHT_DECAY_SKIPPED = 0.02
    MAX_WEIGHT = 1.0
    MIN_WEIGHT = 0.05

    # Drift configuration
    DRIFT_EVIDENCE_WINDOW_DAYS = 7
    DRIFT_MIN_EVIDENCE = 5  # Consecutive picks on new topic to trigger shift

    def __init__(self, profiler: PaperProfiler):
        self._profiler = profiler

    def record_feedback(
        self,
        paper: PaperCandidate,
        action: str,  # "selected", "skipped", "deep_read", "saved", "corrected"
        correction_topic: str = "",
    ) -> dict[str, Any]:
        """Process a single feedback event and update profile.

        Returns a summary of what changed.
        """
        profile = self._profiler.load()
        changes: dict[str, Any] = {"action": action, "paper": paper.title[:100], "weight_changes": {}}

        text = (paper.title + " " + paper.abstract).lower()

        if action == "selected":
            self._boost_topics(profile, text, self.WEIGHT_BOOST_SELECTED, changes)
            self._track_drift(profile, text, action)

        elif action == "deep_read" or action == "saved":
            self._boost_topics(profile, text, self.WEIGHT_BOOST_DEEP_READ, changes)
            # Also add authors as favorites if not already
            for author in paper.authors[:3]:
                if author not in profile.favorite_authors:
                    profile.favorite_authors.append(author)
                    changes.setdefault("new_authors", []).append(author)

        elif action == "skipped":
            self._decay_topics(profile, text, self.WEIGHT_DECAY_SKIPPED, changes)

        elif action == "corrected" and correction_topic:
            # Explicit correction: boost the corrected topic, decay the paper's topics
            for topic in list(profile.topic_weights.keys()):
                if topic.lower() in text:
                    profile.topic_weights[topic] = max(
                        self.MIN_WEIGHT, profile.topic_weights[topic] - 0.10
                    )
                    changes["weight_changes"][topic] = f"-0.10 (corrected)"
            # Add/boost corrected topic
            profile.topic_weights[correction_topic] = min(
                self.MAX_WEIGHT, profile.topic_weights.get(correction_topic, 0.3) + 0.20
            )
            changes["weight_changes"][correction_topic] = "+0.20 (corrected)"

        self._profiler._profile = profile
        self._profiler.save()
        return changes

    def update_drift_state(self) -> DriftState:
        """Re-evaluate drift state based on accumulated evidence."""
        profile = self._profiler.load()
        old_state = profile.drift_state

        if profile.drift_evidence_count >= self.DRIFT_MIN_EVIDENCE:
            if profile.drift_state == DriftState.OBSERVING:
                profile.drift_state = DriftState.SHIFTING
            elif profile.drift_state == DriftState.SHIFTING:
                profile.drift_state = DriftState.RECOVERED
                # Rebalance: integrate new direction into stable profile
                if profile.drift_direction:
                    profile.topic_weights[profile.drift_direction] = 0.5
                    profile.drift_direction = ""
                profile.drift_evidence_count = 0
        elif profile.drift_evidence_count == 0 and profile.drift_state in (
            DriftState.OBSERVING,
            DriftState.SHIFTING,
        ):
            # No evidence recently — revert
            profile.drift_state = DriftState.STABLE
            profile.drift_direction = ""

        if profile.drift_state != old_state:
            logger.info(
                "Drift state transition: %s → %s (evidence=%d)",
                old_state.value, profile.drift_state.value, profile.drift_evidence_count,
            )
        self._profiler._profile = profile
        self._profiler.save()
        return profile.drift_state

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _boost_topics(
        self,
        profile: ResearchProfile,
        text: str,
        amount: float,
        changes: dict[str, Any],
    ) -> None:
        for topic in list(profile.topic_weights.keys()):
            if topic.lower() in text:
                profile.topic_weights[topic] = min(
                    self.MAX_WEIGHT, profile.topic_weights[topic] + amount
                )
                changes["weight_changes"][topic] = f"+{amount:.2f}"

    def _decay_topics(
        self,
        profile: ResearchProfile,
        text: str,
        amount: float,
        changes: dict[str, Any],
    ) -> None:
        for topic in list(profile.topic_weights.keys()):
            if topic.lower() in text:
                profile.topic_weights[topic] = max(
                    self.MIN_WEIGHT, profile.topic_weights[topic] - amount
                )
                changes["weight_changes"][topic] = f"-{amount:.2f}"

    def _track_drift(
        self,
        profile: ResearchProfile,
        text: str,
        action: str,
    ) -> None:
        """Track potential interest drift."""
        # Check if this paper's topics are new (not in existing profile)
        new_topics: list[str] = []
        for topic in profile.topic_weights:
            if topic.lower() in text:
                return  # Known topic — no drift
        # Find new potential topics from paper text
        words = set(text.split())
        for w in words:
            if len(w) > 4 and w not in profile.topic_weights:
                new_topics.append(w)

        if new_topics and action == "selected":
            profile.drift_evidence_count += 1
            if profile.drift_state == DriftState.STABLE:
                profile.drift_state = DriftState.OBSERVING
                profile.drift_direction = new_topics[0]  # First new topic as direction
            # Decay old drift evidence after window
            if profile.drift_evidence_count > self.DRIFT_MIN_EVIDENCE * 2:
                profile.drift_evidence_count = max(0, profile.drift_evidence_count - 1)


# ---------------------------------------------------------------------------
# Daily Curation Pipeline
# ---------------------------------------------------------------------------

class PaperCurationLoop:
    """Complete daily research paper curation loop.

    Usage::

        loop = PaperCurationLoop(workspace="/path/to/workspace")
        loop.init_profile("Single-cell transcriptomics, trajectory inference, ...")
        papers = loop.fetch_candidates()  # From arxiv/biorxiv API
        ranked = loop.rank(papers)
        for paper, score, tier in ranked:
            print(f"[{tier}] {paper.title} ({score:.2f})")
        # After user reads:
        loop.record_feedback(ranked[0][0], "deep_read")
    """

    def __init__(self, workspace_root: str = ""):
        self._workspace = workspace_root or "."
        profile_path = os.path.join(workspace_root, "state", "curation_profile.json")
        self.profiler = PaperProfiler(profile_path)
        self.recommender = DailyRecommender(self.profiler)
        self.learner = FeedbackLearner(self.profiler)

    def init_profile(self, description: str, **kwargs: Any) -> ResearchProfile:
        return self.profiler.init_from_description(description, **kwargs)

    def rank(
        self,
        candidates: list[PaperCandidate],
        top_n: int | None = None,
    ) -> list[tuple[PaperCandidate, float, str]]:
        return self.recommender.rank(candidates, top_n=top_n)

    def record_feedback(
        self,
        paper: PaperCandidate,
        action: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.learner.record_feedback(paper, action, **kwargs)
        self.learner.update_drift_state()
        return result

    def profile_summary(self) -> dict[str, Any]:
        profile = self.profiler.profile
        return {
            "directions": profile.directions,
            "top_topics": sorted(profile.topic_weights.items(), key=lambda x: -x[1])[:10],
            "drift_state": profile.drift_state.value,
            "drift_direction": profile.drift_direction,
            "must_read_keywords": profile.must_read_keywords,
            "favorite_authors": profile.favorite_authors[:5],
        }
