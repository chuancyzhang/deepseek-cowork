import re

APP_VERSION = "4.8.7"


def normalize_version(value):
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    parts = re.findall(r"\d+", text)
    if not parts:
        return ()
    numbers = [int(part) for part in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def compare_versions(left, right):
    left_version = normalize_version(left)
    right_version = normalize_version(right)
    if left_version == right_version:
        return 0
    return 1 if left_version > right_version else -1


def is_newer_version(candidate, current=APP_VERSION):
    candidate_version = normalize_version(candidate)
    current_version = normalize_version(current)
    if not candidate_version or not current_version:
        return False
    return candidate_version > current_version
