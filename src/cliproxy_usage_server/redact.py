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
    """Redact the credential identifier in a `<provider>:<id>` source.

    - id contains '@' (OAuth email)  -> return source unchanged.
    - id has no '@'                  -> treat as API key; rejoin as
                                        f"{provider}:{redact_key(id)}".
    - no ':' separator               -> redact_key on the whole string.
    - Idempotent: any '***' inside the id returns input unchanged.
    """
    if not source:
        return source
    if "***" in source:
        return source
    if ":" not in source:
        return redact_key(source)
    provider, _, ident = source.partition(":")
    if "@" in ident:
        return source
    return f"{provider}:{redact_key(ident)}"
