"""Narrow shared workflow metadata semantics; contents still require inspection."""

import re


def is_operator_authorization_metadata(key: str, *, path: str) -> bool:
    """Recognize the operator's instruction field, never an auth payload field."""
    return (
        key == "operator_authorization"
        and re.fullmatch(r"\$(?:\.steps\[\d+\])?\.metadata(?:_json)?", path) is not None
    )
