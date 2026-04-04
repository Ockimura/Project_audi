#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parent
RESULTS_RE = re.compile(r"^results_(\d{8}_\d{6})$")


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def find_latest_results_dir(root: Path) -> Path:

    candidates: List[Tuple[str, Path]] = []

    for d in root.iterdir():

        if not d.is_dir():
            continue

        m = RESULTS_RE.match(d.name)

        if not m:
            continue

        if (d / "json").is_dir():
            candidates.append((m.group(1), d))

    if not candidates:
        raise FileNotFoundError(f"Не найдено results_*/json в: {root}")

    candidates.sort(key=lambda x: x[0], reverse=True)

    return candidates[0][1]


def safe_get(d: Dict[str, Any], *keys, default=None):

    cur: Any = d

    for k in keys:

        if not isinstance(cur, dict) or k not in cur:
            return default

        cur = cur[k]

    return cur


def to_int(x):

    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


# ------------------------------------------------------------
# main build
# ------------------------------------------------------------

def build_tables(results_dir: Path):

    json_dir = results_dir / "json"
    mhtml_dir = results_dir / "mhtml"

    out_dir = results_dir / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []

    for jp in sorted(json_dir.glob("*.json")):

        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue

        meta = safe_get(data, "meta", default={}) or {}
        dom = safe_get(data, "dom", default={}) or {}
        axe = safe_get(data, "axe", default={}) or {}
        wave = safe_get(data, "wave", default={}) or {}
        manual = safe_get(data, "manual", default={}) or {}

        start_url = meta.get("start_url")
        version = meta.get("version")

        if not start_url:
            continue

        axe_imp = safe_get(axe, "impact_summary", default={}) or {}

        manual_imp = safe_get(manual, "impact_summary", default={}) or {}

        row = {

            "json_stem": jp.stem,
            "start_url": start_url,
            "version": version,

            "navigation_error": data.get("navigation_error"),

            "aim_score": meta.get("aim_score"),

            "dom_total": to_int(dom.get("dom_total")),
            "dom_total_visible": to_int(dom.get("dom_total_visible")),

            "images_visible": to_int(dom.get("images_visible")),
            "links_visible": to_int(dom.get("links_visible")),
            "buttons_visible": to_int(dom.get("buttons_visible")),
            "forms_visible": to_int(dom.get("forms_visible")),

            "axe_nodes_count": to_int(axe.get("nodes_count")),

            "axe_minor": to_int(axe_imp.get("minor")),
            "axe_moderate": to_int(axe_imp.get("moderate")),
            "axe_serious": to_int(axe_imp.get("serious")),
            "axe_critical": to_int(axe_imp.get("critical")),

            "manual_present": isinstance(manual_imp, dict),

            "manual_minor": to_int(manual_imp.get("minor")),
            "manual_moderate": to_int(manual_imp.get("moderate")),
            "manual_serious": to_int(manual_imp.get("serious")),
            "manual_critical": to_int(manual_imp.get("critical")),
            "manual_ruin": to_int(manual_imp.get("ruin")),

        }

        rows.append(row)

    raw_df = pd.DataFrame(rows)


    # ------------------------------------------------------------
    # build pairs
    # ------------------------------------------------------------

    pairs: List[Dict[str, Any]] = []

    for start_url, g in raw_df.groupby("start_url"):

        normal = g[g["version"] == "normal"].head(1)
        low = g[g["version"] == "low_vision"].head(1)

        row: Dict[str, Any] = {
            "start_url": start_url,
            "normal_present": 1 if len(normal) else 0,
            "low_present": 1 if len(low) else 0,
        }

        def pick(col, df):

            if df.empty:
                return None

            return df.iloc[0].get(col)

        for col in raw_df.columns:

            if col in ("start_url", "version"):
                continue

            row[f"normal_{col}"] = pick(col, normal)
            row[f"low_{col}"] = pick(col, low)

        pairs.append(row)

    paired_df = pd.DataFrame(pairs)

    raw_path = out_dir / "raw_pages.csv"
    pairs_path = out_dir / "pairs.csv"

    raw_df.to_csv(raw_path, index=False, encoding="utf-8")
    paired_df.to_csv(pairs_path, index=False, encoding="utf-8")

    return raw_df, paired_df, out_dir


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def main():

    results_dir = find_latest_results_dir(ROOT)

    raw_df, paired_df, out_dir = build_tables(results_dir)

    print("OK")
    print("results:", results_dir)
    print("tables:", out_dir)
    print("raw_pages:", out_dir / "raw_pages.csv")
    print("pairs:", out_dir / "pairs.csv")


if __name__ == "__main__":
    main()