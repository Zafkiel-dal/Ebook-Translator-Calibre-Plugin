"""Token usage tracking and history for translation engines."""

import json
import os
from datetime import datetime

from .utils import log


# Token history file path - stored in calibre plugin config directory
def _get_history_path():
    """Get the path to the token history JSON file."""
    from calibre.constants import config_dir  # type: ignore

    plugin_dir = os.path.join(config_dir, "plugins", "ebook_translator")
    if not os.path.exists(plugin_dir):
        os.makedirs(plugin_dir, exist_ok=True)
    return os.path.join(plugin_dir, "token_history.json")


def load_history():
    """Load token usage history from JSON file."""
    path = _get_history_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"entries": [], "totals": {}}


def save_history(history):
    """Save token usage history to JSON file."""
    path = _get_history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except IOError as e:
        log.debug("Failed to save token history: %s" % str(e))


def log_token_usage(
    engine_name,
    book_title,
    model,
    input_tokens,
    output_tokens,
    total_tokens,
    is_batch=False,
    thinking_tokens=0,
):
    """Log a single translation's token usage to history."""
    history = load_history()
    entry = {
        "date": datetime.now().isoformat(),
        "engine": engine_name,
        "book": book_title or "Unknown",
        "model": model or "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "thinking_tokens": thinking_tokens or 0,
        "batch": is_batch,
    }
    history["entries"].append(entry)

    # Update totals per engine
    totals = history.get("totals", {})
    engine_total = totals.get(engine_name)
    if engine_total is None:
        engine_total = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "thinking_tokens": 0, "count": 0,
        }
    else:
        # Ensure all keys exist (handles legacy history files without thinking_tokens)
        engine_total.setdefault("input_tokens", 0)
        engine_total.setdefault("output_tokens", 0)
        engine_total.setdefault("total_tokens", 0)
        engine_total.setdefault("thinking_tokens", 0)
        engine_total.setdefault("count", 0)
    engine_total["input_tokens"] += input_tokens
    engine_total["output_tokens"] += output_tokens
    engine_total["total_tokens"] += total_tokens
    engine_total["thinking_tokens"] += thinking_tokens or 0
    engine_total["count"] += 1

    totals[engine_name] = engine_total
    history["totals"] = totals

    save_history(history)
    thinking_str = ""
    if thinking_tokens:
        thinking_str = ", %d thinking" % thinking_tokens
    log.debug(
        "Token usage logged: %s - %d tokens (%d in, %d out%s)"
        % (engine_name, total_tokens, input_tokens, output_tokens, thinking_str)
    )


def get_total_tokens(engine_name=None):
    """Get total tokens used, optionally filtered by engine."""
    history = load_history()
    if engine_name:
        engine_total = history.get("totals", {}).get(engine_name, {})
        return engine_total.get("total_tokens", 0)
    return sum(t.get("total_tokens", 0) for t in history.get("totals", {}).values())


def format_number(n):
    """Format a number with commas for readability."""
    if n >= 1000000:
        return "%.1fM" % (n / 1000000)
    elif n >= 1000:
        return "%.1fK" % (n / 1000)
    return str(n)
