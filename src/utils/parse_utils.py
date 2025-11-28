# src/utils/parse_utils.py
import re
import json
from typing import Tuple, Optional, Any

def extract_json_from_text(text: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Try to find JSON object/list in a longer LLM response.
    Returns (parsed_json, error_message)
    """
    if not text:
        return None, "empty text"
    # Common pattern: find first { ... } or [ ... ] block with balanced braces
    # We'll try to find json-like substrings and parse them.
    # 1. Try direct parse
    try:
        return json.loads(text), None
    except Exception:
        pass

    # 2. find substring starting at first { or [
    starts = [m.start() for m in re.finditer(r'[\{\[]', text)]
    for s in starts:
        for e in range(len(text)-1, s, -1):
            candidate = text[s:e+1]
            try:
                parsed = json.loads(candidate)
                return parsed, None
            except Exception:
                continue
    # 3. fallback: try to extract with regex that matches {...}
    m = re.search(r'(\{.*\}|\[.*\])', text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0)), None
        except Exception as ex:
            return None, f"regex-extracted JSON invalid: {ex}"
    return None, "no JSON found"
