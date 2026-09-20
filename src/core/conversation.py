"""What was asked a moment ago, so the next question can lean on it.

A question asked out loud is rarely self-contained -- "how many of those
merged?", "and the one before that?" -- and every question used to start from
nothing. This keeps the last few turns, and forgets them once you've clearly
moved on, so an unrelated question hours later isn't answered in the light of a
conversation you don't remember having.
"""

import threading
import time
from dataclasses import dataclass, field

# a gap this long means the conversation is over. Long enough to think, read the
# answer and follow up; short enough that tonight's question isn't dragged into
# tomorrow's
IDLE_RESET_SECONDS = 10 * 60
# how many turns the model is shown. The whole point is resolving "those" and
# "it", which reach back a turn or two, not ten
MAX_TURNS = 3


@dataclass
class Turn:
    question: str
    spoken: str
    item_ids: list[str] = field(default_factory=list)
    asked_at: float = 0.0


class Conversation:
    """The turns of the conversation in progress. Safe to use from any thread."""

    def __init__(self, idle_seconds: float = IDLE_RESET_SECONDS):
        self._idle_seconds = idle_seconds
        self._turns: list[Turn] = []
        self._last_at = 0.0
        self._lock = threading.Lock()

    def _expired(self, now: float) -> bool:
        return bool(self._turns) and now - self._last_at > self._idle_seconds

    def recent(self, now: float | None = None) -> list[Turn]:
        """The turns a new question should be read against -- none, once the
        conversation has gone cold."""
        now = time.time() if now is None else now
        with self._lock:
            if self._expired(now):
                self._turns = []
            return list(self._turns[-MAX_TURNS:])

    def add(self, question: str, spoken: str, item_ids: list[str], now: float | None = None):
        now = time.time() if now is None else now
        with self._lock:
            if self._expired(now):
                self._turns = []
            self._turns.append(Turn(question, spoken, list(item_ids), now))
            self._last_at = now

    def reset(self):
        with self._lock:
            self._turns = []
            self._last_at = 0.0

    @property
    def active(self) -> bool:
        return bool(self.recent())
