import re
import ofscraper.utils.paths.common as paths
from typing import Any

# --- Default Patterns (Regex or Plain Text) ---
_DEFAULT_PATTERNS = {
    # Redacts API keys and session tokens (Fixed to stop at whitespace)
    r"&Policy=[^&\"'\s]+": "&Policy={hidden}",
    r"&Signature=[^&\"'\s]+": "&Signature={hidden}",
    r"&Key-Pair-Id=[^&\"'\s]+": "&Key-Pair-Id={hidden}",
    # Redacts common sensitive info
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b": "{ip_address}",  # IP Addresses
    r"\"sess\":\"[^\"]+\"": '"sess":"{hidden}"',  # Session tokens
}

# This dictionary will be populated at runtime by your add_sensitive_pattern function
_USER_ADDED_PATTERNS = {}


def add_sensitive_pattern(pattern: Any, replacement: Any = "{hidden}"):
    """
    Dynamically adds a new pattern. It intelligently handles strings,
    numbers, and compiled regex objects to prevent errors.
    """
    key = ""
    # If the pattern is a compiled regex object (re.Pattern)
    if isinstance(pattern, re.Pattern):
        # Use its .pattern attribute to get the original string
        key = pattern.pattern
    else:
        # For anything else (numbers, strings), use str() as a safe fallback
        key = str(pattern)

    _USER_ADDED_PATTERNS[key] = str(replacement)


def getSenstiveDict() -> dict:
    """
    Returns a combined dictionary of default, user-added, and dynamic patterns.
    """
    # Patterns added during the session (exact secret values such as the
    # session cookie) go first: a broader rule applied earlier, like the home
    # folder name, could rewrite part of a secret so it no longer matches.
    # Dicts keep insertion order, which is the order they're applied in.
    combined_patterns = _USER_ADDED_PATTERNS.copy()
    for pattern, replacement in _DEFAULT_PATTERNS.items():
        combined_patterns.setdefault(pattern, replacement)

    # --- Add Dynamic Patterns ---
    # These are generated based on the current user's specific info.

    # 2. Redact profile/download directory paths
    profile_dir = paths.get_username()
    if profile_dir:
        # Escape the path for regex and replace it
        combined_patterns.setdefault(re.escape(str(profile_dir)), "{home_directory}")

    return combined_patterns
