import math
from collections import deque


class TokenSpeedTracker:
    """Track estimated streamed output speed for one session at runtime."""

    def __init__(self, window_seconds=3.0):
        self.window_seconds = max(0.1, float(window_seconds))
        self.clear()

    def clear(self):
        self.active = False
        self.request_id = ""
        self.first_text_at = None
        self.estimated_tokens = 0
        self._non_ascii = 0
        self._ascii_nonspace = 0
        self._samples = deque()
        self.last_rate = None
        self.last_tokens = 0
        self.last_duration = 0.0
        self.last_request_id = ""
        self.unavailable_reason = ""

    def begin(self, request_id, now):
        request_id = str(request_id or "").strip()
        if not request_id:
            raise ValueError("Token speed monitoring requires a provider request_id.")
        if self.active:
            raise RuntimeError(
                "Token speed provider request started before the active request finished: "
                f"active={self.request_id!r}, received={request_id!r}."
            )
        self.active = True
        self.request_id = request_id
        self.first_text_at = None
        self.estimated_tokens = 0
        self._non_ascii = 0
        self._ascii_nonspace = 0
        self._samples.clear()
        self.unavailable_reason = ""
        return self.snapshot(now)

    def retry(self, request_id, now):
        self._require_active_request(request_id)
        self.first_text_at = None
        self.estimated_tokens = 0
        self._non_ascii = 0
        self._ascii_nonspace = 0
        self._samples.clear()
        return self.snapshot(now)

    def record_text(self, text, now):
        if not self.active:
            raise RuntimeError("Token speed text arrived without an active provider request.")
        text = str(text or "")
        if not text:
            return False

        previous_estimate = self.estimated_tokens
        for char in text:
            if char.isspace():
                continue
            if ord(char) > 127:
                self._non_ascii += 1
            else:
                self._ascii_nonspace += 1
        self.estimated_tokens = self._non_ascii + int(
            math.ceil(self._ascii_nonspace / 4.0)
        )
        added_tokens = max(0, self.estimated_tokens - previous_estimate)
        if added_tokens <= 0:
            return False

        timestamp = float(now)
        first_sample = self.first_text_at is None
        if first_sample:
            self.first_text_at = timestamp
        self._samples.append((timestamp, added_tokens))
        self._prune(timestamp)
        return first_sample

    def finish(self, request_id, now, status="completed"):
        self._require_active_request(request_id)
        timestamp = float(now)
        completed = str(status or "").strip().lower() == "completed"
        if completed and self.first_text_at is not None and self.estimated_tokens > 0:
            duration = max(0.0, timestamp - self.first_text_at)
            if duration > 0:
                self.last_rate = self.estimated_tokens / duration
                self.last_tokens = self.estimated_tokens
                self.last_duration = duration
                self.last_request_id = self.request_id
        self.active = False
        self.request_id = ""
        self.first_text_at = None
        self.estimated_tokens = 0
        self._non_ascii = 0
        self._ascii_nonspace = 0
        self._samples.clear()
        return self.snapshot(timestamp)

    def cancel(self, now):
        self.active = False
        self.request_id = ""
        self.first_text_at = None
        self.estimated_tokens = 0
        self._non_ascii = 0
        self._ascii_nonspace = 0
        self._samples.clear()
        return self.snapshot(now)

    def fail(self, reason, now):
        self.cancel(now)
        self.unavailable_reason = str(reason or "Token speed monitoring failed.").strip()
        return self.snapshot(now)

    def current_rate(self, now):
        if not self.active or self.first_text_at is None:
            return None
        timestamp = float(now)
        self._prune(timestamp)
        elapsed = min(
            self.window_seconds,
            max(0.0, timestamp - self.first_text_at),
        )
        if elapsed <= 0:
            return None
        return sum(tokens for _sample_at, tokens in self._samples) / elapsed

    def snapshot(self, now):
        return {
            "active": bool(self.active),
            "request_id": self.request_id,
            "current_rate": self.current_rate(now),
            "current_tokens": int(self.estimated_tokens),
            "last_rate": self.last_rate,
            "last_tokens": int(self.last_tokens),
            "last_duration": float(self.last_duration),
            "last_request_id": self.last_request_id,
            "unavailable_reason": self.unavailable_reason,
        }

    def _prune(self, now):
        cutoff = float(now) - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _require_active_request(self, request_id):
        request_id = str(request_id or "").strip()
        if not self.active:
            raise RuntimeError("Token speed request event arrived without an active request.")
        if request_id != self.request_id:
            raise RuntimeError(
                "Token speed request_id mismatch: "
                f"active={self.request_id!r}, received={request_id!r}."
            )
