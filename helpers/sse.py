"""SSE (Server-Sent Events) utility functions."""

import json


def _sse(data: dict) -> bytes:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _extract_reply_delta(accumulated: str, last_len: int) -> str:
    """Try to parse accumulated JSON text and extract the reply field's new portion."""
    try:
        idx = accumulated.find('"reply"')
        if idx < 0:
            return ""
        colon = accumulated.index(":", idx + 7)
        quote_start = accumulated.index('"', colon + 1)
        reply_so_far = accumulated[quote_start + 1:]
        reply_so_far = reply_so_far.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        if len(reply_so_far) > last_len:
            return reply_so_far[last_len:]
    except (ValueError, IndexError):
        pass
    return ""
