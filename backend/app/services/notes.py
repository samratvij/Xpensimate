def clean_note(text: str | None) -> str | None:
    """Trim a free-text note; blank means no note."""
    return (text or "").strip() or None
