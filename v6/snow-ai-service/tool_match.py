"""
Plain-English request -> candidate tools, before the AI picks one (MCP Chat and the AI Agent tool picker).

Exact word overlap missed obvious requests ("Which servers are not mapped to anything?" scored 0 for
find_unmapped_cis: servers != server, mapped != unmapped), so the AI never saw the right tool and the chat fell back
to a general answer. Words are now reduced to a simple stem and also match when one contains the other.
"""

import math
import re
from typing import Iterable, List, Set

STOP = set("a an the to of for in on and or with my me i is are be please show give get all what which how many "
           "can you from by at as it this that not no any anything there do does have has who list find tell "
           "about".split())


def stem(word: str) -> str:
    w = word.lower()
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: len(w) - len(suffix)] + keep
    return w


def words(text: str) -> Set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOP or len(w) <= 1:
            continue
        out.add(stem(w))
        if w.startswith("un") and len(w) > 5:   # "unmapped" also answers "not mapped"
            out.add(stem(w[2:]))
    return out


def _hits(request: Set[str], tool_words: Iterable[str]) -> float:
    """Request words found in the tool words: 1 when equal, 0.5 when one contains the other (both 4+ letters)."""
    tool_words = set(tool_words)
    n = 0.0
    for r in request:
        if r in tool_words:
            n += 1
        elif len(r) >= 4 and any(len(t) >= 4 and (r in t or t in r) for t in tool_words):
            n += 0.5
    return n


def score(message_words: Set[str], name: str, description: str) -> float:
    name_words = words(name.replace("snow_", "").replace("_", " "))
    desc_words = words((description or "")[:400])
    return 3 * _hits(message_words, name_words) + _hits(message_words, desc_words)


# Words people use for the same thing ("CI" is a CMDB record)
SYNONYMS = {"ci": {"cmdb"}, "cmdb": {"ci"}, "configuration": {"ci", "cmdb"}}


def _match(r: str, tool_words: Set[str]) -> float:
    if r in tool_words:
        return 1.0
    if len(r) >= 4 and any(len(t) >= 4 and (r in t or t in r) for t in tool_words):
        return 0.5
    return 0.0


def rank(message: str, tools: List[dict], k: int, skip: Iterable[str] = ()) -> List[dict]:
    """tools: dicts with 'name' and 'description'. Best first; ties go to the shorter name.

    Each request word is weighted by how rare it is among the tools (a word like 'create' or 'list' that appears in
    a hundred tools says little; 'server' or 'ci' says a lot), so generic verbs cannot outrank the words that
    identify the right tool."""
    msg = words(message)
    for w in list(msg):
        msg |= SYNONYMS.get(w, set())
    skip = set(skip)
    pool = [t for t in tools if t.get("name") not in skip]
    if not pool or not msg:
        return []
    parsed = [(t, words(t["name"].replace("snow_", "").replace("_", " ")), words((t.get("description") or "")[:400]))
              for t in pool]
    n = len(parsed)
    weight = {}
    for r in msg:
        df = sum(1 for _, nw, dw in parsed if _match(r, nw | dw))
        weight[r] = math.log((n + 1) / (df + 0.5)) if df else 0.0
    scored = []
    for t, nw, dw in parsed:
        s = sum(weight[r] * (3 * _match(r, nw) + _match(r, dw)) for r in msg)
        if s > 0:
            scored.append((s, len(t["name"]), t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, _, t in scored[:k]]
