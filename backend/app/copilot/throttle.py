"""Per-merchant limit on Copilot questions (each one can trigger several model calls)."""

import time

QUESTIONS = 20
WINDOW_SECONDS = 600


class QuestionThrottle:
    def __init__(self, questions: int = QUESTIONS, window: int = WINDOW_SECONDS) -> None:
        self._questions = questions
        self._window = window
        self._asked: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a question and return True, or return False when the key is over its limit."""
        current = time.time() if now is None else now
        recent = [t for t in self._asked.get(key, []) if current - t < self._window]
        if len(recent) >= self._questions:
            self._asked[key] = recent
            return False
        recent.append(current)
        self._asked[key] = recent
        return True
