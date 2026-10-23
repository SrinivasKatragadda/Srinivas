"""
Architecture diagram for the TSD.

The AI returns a structured spec (layers -> components, plus flows between components);
this module draws it as a PNG (embedded in the Word document) and as aligned ASCII
(fallback / on-screen text). Drawing is deterministic, so boxes never come out misaligned.

spec = {
  "title": "...",
  "layers": [{"name": "Users & Channels", "components": ["End users", "Service desk agents"]}, ...],
  "flows":  [{"from": "Service Portal", "to": "Incident Management", "label": "submit"}]
}
"""

from __future__ import annotations

import io
import textwrap
from typing import Any, Dict, List

PALETTE = ["#1E5EBF", "#0F766E", "#7C3AED", "#B45309", "#475569", "#BE123C", "#15803D"]
TINTS = ["#E8F0FE", "#E6F4F1", "#F1EAFE", "#FDF1E3", "#EEF1F5", "#FDE8EC", "#E7F6EC"]


def normalize_spec(spec: Any) -> Dict[str, Any]:
    spec = spec if isinstance(spec, dict) else {}
    layers = []
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("name"):
            comps = [str(c.get("name") if isinstance(c, dict) else c).strip() for c in (layer.get("components") or [])]
            comps = [c for c in comps if c][:6]
            if comps:
                layers.append({"name": str(layer["name"]).strip()[:40], "components": comps})
    names = {c for l in layers for c in l["components"]}
    flows = []
    for f in spec.get("flows") or []:
        if isinstance(f, dict) and f.get("from") in names and f.get("to") in names and f["from"] != f["to"]:
            flows.append({"from": f["from"], "to": f["to"], "label": str(f.get("label") or "")[:24]})
    return {"title": str(spec.get("title") or "Solution Architecture")[:90], "layers": layers[:6], "flows": flows[:10]}


def render_png(spec: Dict[str, Any], dpi: int = 200, fmt: str = "png") -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    spec = normalize_spec(spec)
    layers = spec["layers"]
    if not layers:
        raise ValueError("architecture spec has no layers")
    W, band_h, gap, label_w = 16.0, 1.55, 0.6, 3.3
    H = len(layers) * band_h + (len(layers) - 1) * gap + 1.2
    fig, ax = plt.subplots(figsize=(W / 1.6, H / 1.6))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    ax.text(W / 2, H - 0.35, spec["title"], ha="center", va="center", fontsize=12, fontweight="bold", color="#0A1628")

    centers: Dict[str, tuple] = {}
    band_mid = []
    for i, layer in enumerate(layers):
        top = H - 0.8 - i * (band_h + gap)
        y0 = top - band_h
        col, tint = PALETTE[i % len(PALETTE)], TINTS[i % len(TINTS)]
        ax.add_patch(FancyBboxPatch((0.15, y0), W - 0.3, band_h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc=tint, ec=col, lw=1.2, zorder=0))
        ax.text(0.35, y0 + band_h / 2, "\n".join(textwrap.wrap(layer["name"], 13)), ha="left", va="center",
                fontsize=8, fontweight="bold", color=col, zorder=4)
        comps = layer["components"]
        avail = W - label_w - 0.3
        slot = avail / len(comps)
        bw, bh = min(slot - 0.25, 3.2), band_h - 0.5
        for j, name in enumerate(comps):
            cx = label_w + slot * j + slot / 2
            cy = y0 + band_h / 2
            ax.add_patch(FancyBboxPatch((cx - bw / 2, cy - bh / 2), bw, bh,
                                        boxstyle="round,pad=0.02,rounding_size=0.1", fc="white", ec=col, lw=1.1,
                                        zorder=3))
            ax.text(cx, cy, "\n".join(textwrap.wrap(name, max(10, int(bw * 5.2)))[:3]), ha="center", va="center",
                    fontsize=7.2, color="#0A1628", zorder=4)
            centers[name] = (cx, cy, bw, bh)
        band_mid.append((y0, top))

    # layer-to-layer connectors (generic direction of travel)
    for i in range(len(layers) - 1):
        y_from, y_to = band_mid[i][0], band_mid[i + 1][1]
        ax.add_patch(FancyArrowPatch((1.0, y_from), (1.0, y_to), arrowstyle="-|>",
                                     mutation_scale=10, color="#94A3B8", lw=1.0, zorder=1))

    # specific flows: numbered badges on both ends + legend; a line only where it cannot cross other boxes
    layer_of = {c: i for i, l in enumerate(layers) for c in l["components"]}
    pos_of = {c: j for l in layers for j, c in enumerate(l["components"])}
    for k, f in enumerate(spec["flows"], 1):
        (x1, y1, w1, h1), (x2, y2, w2, h2) = centers[f["from"]], centers[f["to"]]
        li, lj = layer_of[f["from"]], layer_of[f["to"]]
        straight = abs(li - lj) == 1 or (li == lj and abs(pos_of[f["from"]] - pos_of[f["to"]]) == 1)
        if straight:
            if li == lj:
                d = 1 if x2 > x1 else -1
                start, end = (x1 + d * w1 / 2, y1), (x2 - d * w2 / 2, y2)
            else:
                start = (x1, y1 - h1 / 2) if lj > li else (x1, y1 + h1 / 2)
                end = (x2, y2 + h2 / 2) if lj > li else (x2, y2 - h2 / 2)
            ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11, color="#1E293B",
                                         lw=1.1, zorder=5, shrinkA=0, shrinkB=1))
        for (x, y, w, h), where in (((x1, y1, w1, h1), "src"), ((x2, y2, w2, h2), "dst")):
            bx = x + w / 2 - 0.12 if where == "src" else x - w / 2 + 0.12
            ax.text(bx, y + h / 2, str(k), fontsize=6, fontweight="bold", color="white", ha="center", va="center",
                    zorder=7, bbox=dict(boxstyle="circle,pad=0.18", fc="#1E293B" if where == "src" else "#B45309",
                                        ec="none"))
    if spec["flows"]:
        y = -0.25
        ax.text(0.2, y, "Key flows  (dark badge = from, amber badge = to)", fontsize=7.5, fontweight="bold",
                color="#0A1628", va="top")
        per_col = (len(spec["flows"]) + 1) // 2
        for k, f in enumerate(spec["flows"], 1):
            col, row = (k - 1) // per_col, (k - 1) % per_col
            ax.text(0.2 + col * W / 2, y - 0.42 - row * 0.36,
                    f"{k}.  {f['from']}  →  {f['to']}" + (f"   ({f['label']})" if f["label"] else ""),
                    fontsize=6.8, color="#1E293B", va="top")
        ax.set_ylim(y - 0.6 - per_col * 0.36, H)
        fig.set_size_inches(W / 1.6, (H + 0.9 + per_col * 0.36) / 1.6)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def render_ascii(spec: Dict[str, Any], width: int = 78) -> str:
    spec = normalize_spec(spec)
    inner = width - 4
    out: List[str] = []
    for i, layer in enumerate(spec["layers"]):
        out.append("+" + "-" * (width - 2) + "+")
        out.append("| " + layer["name"].upper()[:inner].ljust(inner) + " |")
        line = "  ".join(f"[{c}]" for c in layer["components"])
        for chunk in textwrap.wrap(line, inner) or [""]:
            out.append("| " + chunk.ljust(inner) + " |")
        out.append("+" + "-" * (width - 2) + "+")
        if i < len(spec["layers"]) - 1:
            out.append(" " * (width // 2) + "|")
            out.append(" " * (width // 2) + "v")
    if spec["flows"]:
        out.append("")
        out.append("Key flows:")
        out += [f"  {f['from']} --{f['label'] or ''}--> {f['to']}" for f in spec["flows"]]
    return "\n".join(out)
