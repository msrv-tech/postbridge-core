"""Общие утилиты для rule-based адаптации текста."""


def truncate_at_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[: max_len + 1].rsplit(maxsplit=1)[0]
    return truncated[:max_len] if len(truncated) > max_len else truncated
