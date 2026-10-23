"""
Subflow knowledge base (from now3.py): remembers every subflow the agent built so a later plan can reuse a
published subflow instead of building it again.

Kept per ServiceNow instance in jobs/subflow_kb.json. Matching is bag-of-words cosine similarity, and a match
is only offered when it also contains every action type the new subflow needs — the caller must still confirm
the subflow exists in the instance before wiring it.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .actions import ui_action_name

_HERE = Path(__file__).resolve().parent
KB_FILE = Path(os.getenv("FLOW_KB_FILE", _HERE.parent / "jobs" / "subflow_kb.json"))
SEED_FILE = _HERE / "seed_subflows.json"
THRESHOLD = float(os.getenv("FLOW_KB_THRESHOLD", "0.45"))

_STOP = set("a an the to for of in on at and or with is are be by from this that into your you it as we will "
            "when if then also so do does sub subflow handler ai".split())
_lock = threading.Lock()


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(a[w] * b[w] for w in set(a) & set(b))
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _key(instance: str) -> str:
    return (instance or "").strip().rstrip("/").lower()


def action_types(subflow: dict) -> List[str]:
    return [ui_action_name(a.get("type", "")) for a in subflow.get("actions") or []]


def fingerprint(subflow: dict) -> str:
    """Text used for similarity: name, description, action types and their key inputs, inputs/outputs."""
    parts = [subflow.get("name", ""), subflow.get("description", "")]
    for a in subflow.get("actions") or []:
        parts.append(ui_action_name(a.get("type", "")))
        for k, v in (a.get("inputs") or {}).items():
            if k in ("subject", "short_description", "log_message", "message", "table") and isinstance(v, str):
                parts.append(v)
    for io in ("inputs", "outputs"):
        for f in subflow.get(io) or []:
            parts += [f.get("label", ""), f.get("name", "")]
    return " ".join(p for p in parts if p)


class SubflowKB:
    def __init__(self, path: Path = KB_FILE):
        self.path = Path(path)
        self.data: Dict[str, List[dict]] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                return
            except Exception:
                self.data = {}
        # First run: import the library that came with the standalone flow agent (now3.py).
        if SEED_FILE.exists():
            try:
                seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
                self.data = {_key(k): v for k, v in seed.items()}
            except Exception:
                self.data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def entries(self, instance: str) -> List[dict]:
        return list(self.data.get(_key(instance), []))

    def add(self, instance: str, published_name: str, subflow: dict, sys_id: str = ""):
        entry = {"published_name": published_name, "sys_id": sys_id,
                 "description": subflow.get("description", ""), "text": fingerprint(subflow),
                 "action_types": action_types(subflow),
                 "inputs": subflow.get("inputs") or [], "outputs": subflow.get("outputs") or []}
        with _lock:
            rows = [e for e in self.data.get(_key(instance), []) if e["published_name"] != published_name]
            rows.append(entry)
            self.data[_key(instance)] = rows
            self._save()

    def remove(self, instance: str, published_name: str) -> bool:
        with _lock:
            rows = self.data.get(_key(instance), [])
            keep = [e for e in rows if e["published_name"] != published_name]
            if len(keep) == len(rows):
                return False
            self.data[_key(instance)] = keep
            self._save()
            return True

    def search(self, instance: str, subflow: dict, top_k: int = 3) -> List[Tuple[float, dict]]:
        q = Counter(_tokens(fingerprint(subflow)))
        scored = [(_cosine(q, Counter(_tokens(e.get("text", "")))), e) for e in self.entries(instance)]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: -s[0])
        return scored[:top_k]

    def best_match(self, instance: str, subflow: dict, threshold: float = THRESHOLD) -> Optional[Tuple[float, dict]]:
        """Best entry above the threshold that also has every action type the new subflow needs."""
        need = set(action_types(subflow)) - {"Log"}
        for score, e in self.search(instance, subflow, top_k=5):
            if score < threshold:
                break
            if need <= set(e.get("action_types") or []):
                return score, e
        return None
