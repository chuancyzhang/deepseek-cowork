"""Decode IM HTTP responses while keeping non-JSON failure details readable."""


def parse_response_json(response):
    try:
        return response.json()
    except ValueError:
        text = response.text or ""
        return text[:800] + (f"...<truncated {len(text)}>" if len(text) > 800 else "")
