def redact_key(key: str) -> str:
    """Redact an API key for safe display.

    Rules:
      - 3+ dash-parts  → "{first}-*******-{last}"
      - <= 2 parts    → "*******{last 4 chars of the whole key}"
      - Idempotent: redacting a redacted value returns the same value.
    """
    parts = key.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-*******-{parts[-1]}"
    # Strip a leading "*******" prefix so repeated calls are idempotent:
    # the slice is computed from the same normalized base regardless of whether
    # the input is raw or already-redacted.
    base = key.removeprefix("*******")
    return f"*******{base[-4:]}"


def redact_source(source: str) -> str:
    """Redact a credential source for safe display.

    OAuth sources are email-like values and pass through unchanged. All
    non-email values are treated as opaque keys/secrets and redacted as a
    single string.
    """
    if not source:
        return source
    if "***" in source:
        return source
    if "@" in source:
        return source
    return redact_key(source)
