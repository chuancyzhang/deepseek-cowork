"""Shared standard-package rules; only the Python standard library is required."""
import json

DEFAULT_CONFIG_PATH = "_internal/config.json"
MAX_DEFAULT_CONFIG_BYTES = 8192


def validate_default_config(content):
    """Allow only the shipped, empty default configuration, never user settings."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key")
            result[key] = value
        return result

    if len(content) > MAX_DEFAULT_CONFIG_BYTES:
        raise ValueError("Default configuration is too large")
    value = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object)
    if value != {"api_key": "", "base_url": "https://api.deepseek.com", "disabled_skills": []}:
        raise ValueError("Configuration is not the empty shipped default")
