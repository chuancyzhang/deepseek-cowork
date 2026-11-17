"""Small deterministic bilingual matcher shared by skills and tools."""
import re
import unicodedata


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def tokens(text):
    result = set()
    for word in re.findall(r"[a-z0-9][a-z0-9_-]*|[\u3400-\u9fff]+", normalize(text)):
        result.add(word)
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            # Bigrams match Chinese task phrases without single-character noise.
            result.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.update(re.split(r"[_-]", word))
    return result - {"", "the", "a", "to", "and", "of", "for"}


def phrase_in(phrase, text):
    phrase, text = normalize(phrase), normalize(text)
    if not phrase:
        return False
    if re.search(r"[\u3400-\u9fff]", phrase):
        return phrase in text
    return re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", text) is not None


def score(query, *, names=(), phrases=(), description="", body="", anti=()):
    query_tokens = tokens(query)
    if not query_tokens:
        return 0.0
    exact = any(phrase_in(name, query) for name in names)
    if not exact and any(phrase_in(item, query) for item in anti):
        return 0.0
    value = 100.0 if exact else 0.0
    value += 20 * sum(phrase_in(item, query) for item in phrases)
    value += 8 * len(query_tokens & tokens(" ".join([*names, *phrases])))
    value += 3 * len(query_tokens & tokens(description))
    # A long instruction must not outrank a task-specific name or trigger.
    value += min(2.0, 0.25 * len(query_tokens & tokens(body)))
    return value
