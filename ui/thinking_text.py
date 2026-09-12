"""Append-only UI reasoning text; persistence still receives ordinary strings."""

import copy

from PySide6.QtGui import Qt as TextQt


class ThinkingTextBuffer:
    def __init__(self, text=""):
        self._parts = []
        self._length = 0
        self._cached_text = ""
        self.has_text = False
        self.append(text)

    def __len__(self):
        return self._length

    @property
    def version(self):
        return len(self._parts)

    def append(self, text):
        text = str(text or "")
        if not text:
            return
        self._parts.append(text)
        self._length += len(text)
        self.has_text = self.has_text or bool(text.strip())
        self._cached_text = None

    def text(self):
        if self._cached_text is None:
            self._cached_text = "".join(self._parts)
        return self._cached_text

    def text_since(self, version):
        return "".join(self._parts[version:])


class ThinkingAutoTextProbe:
    """Cache Qt's AutoText decision without rescanning the reasoning body.

    Only the first nonblank line or first complete tag can affect the usual
    Qt heuristic. XML declarations retain Qt's full-prefix detection because
    the following content can still change the result. Qt itself decides
    which tags and entities count as rich text.
    """

    def __init__(self):
        self._prefix = ThinkingTextBuffer()
        self._opening = ""
        self._started = False
        self._tag_seen = False
        self._entity_seen = False
        self._xml = False
        self._complete = False
        self._checked_version = -1
        self._rich = False

    def append(self, text):
        if self._complete:
            return
        if self._xml:
            self._prefix.append(text)
            return
        piece = []
        for index, char in enumerate(text):
            if not self._started:
                # QChar::isSpace does not include Python's four extra ASCII
                # information separators.
                if char.isspace() and char not in "\x1c\x1d\x1e\x1f":
                    continue
                self._started = True
            piece.append(char)
            if len(self._opening) < 5:
                self._opening += char
                if self._opening == "<?xml":
                    self._xml = True
                    piece.append(text[index + 1:])
                    break
            self._tag_seen = self._tag_seen or char == "<"
            self._entity_seen = self._entity_seen or char == "&"
            if (char == "\n" and not self._tag_seen) or (char == ">" and self._tag_seen):
                self._complete = True
                break
        self._prefix.append("".join(piece))

    def is_rich_text(self):
        if not self._tag_seen and not self._entity_seen:
            return False
        if self._checked_version != self._prefix.version:
            self._rich = TextQt.mightBeRichText(self._prefix.text())
            self._checked_version = self._prefix.version
            if self._rich:
                self._complete = True
        return self._rich


_THINKING_TEXT_BUFFER = "_thinking_text_buffer"


def append_thinking_event_text(event, delta):
    buffer = event.get(_THINKING_TEXT_BUFFER)
    if buffer is None:
        buffer = ThinkingTextBuffer(event.get("text") or "")
        event[_THINKING_TEXT_BUFFER] = buffer
    buffer.append(delta)


def thinking_event_text(event):
    buffer = event.get(_THINKING_TEXT_BUFFER)
    return buffer.text() if buffer is not None else str(event.get("text") or "")


def finish_thinking_event_text(event):
    buffer = event.get(_THINKING_TEXT_BUFFER)
    if buffer is not None:
        event["text"] = buffer.text()
        del event[_THINKING_TEXT_BUFFER]


def snapshot_thinking_timeline(events):
    """Detach a complete v1 snapshot, including any still-open thinking event."""
    result = []
    for event in events or []:
        if not isinstance(event, dict):
            result.append(copy.deepcopy(event))
            continue
        snapshot = copy.deepcopy({
            key: value for key, value in event.items()
            if key != _THINKING_TEXT_BUFFER
        })
        if _THINKING_TEXT_BUFFER in event:
            snapshot["text"] = thinking_event_text(event)
        result.append(snapshot)
    return result
