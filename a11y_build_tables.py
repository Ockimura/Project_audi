#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
a11y_build_tables.py

Сбор таблиц из results_*/json (+ wave mhtml при необходимости).
- Авто-поиск последней results_YYYYMMDD_HHMMSS
- MHTML имена: <md5>_<version>.mhtml
- Пишет:
  - tables/raw_pages.csv
  - tables/pairs.csv

Добавлено:
- dom_total и групповые *_total метрики (не visible)
- dom_noimg_total
- headings_total, landmarks_total
- ratios *_total
- manual_reason
- manual_ruin
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# -------------------------
# find latest results_*
# -------------------------
RESULTS_RE = re.compile(r"^results_(\d{8}_\d{6})$")
ROOT = Path(__file__).resolve().parent


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


def infer_analysis_dir(results_dir: Path) -> Path:
    return results_dir / "tables"


# -------------------------
# wave parsing from mhtml
# -------------------------
WAVE_NUM_KEYS = [
    "Wave_Error",
    "Wave_Contrast_Error",
    "Wave_Alerts",
    "Wave_Aria",
    "Wave_Features",
    "Wave_Structure",
]

_WAVE_PATTERNS = {
    "Wave_Error": r'id\s*=\s*["\']error["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "Wave_Contrast_Error": r'id\s*=\s*["\']contrastnum["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "Wave_Alerts": r'id\s*=\s*["\']alert["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "Wave_Features": r'id\s*=\s*["\']feature["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "Wave_Structure": r'id\s*=\s*["\']structure["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "Wave_Aria": r'id\s*=\s*["\']aria["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    "aim_score": r'id\s*=\s*["\']aim_score["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
}


def parse_mhtml_stem(stem: str) -> Optional[Tuple[str, str]]:
    """
    ожидаем: <md5>_<version>
    например: ab12cd34_low_vision
    """
    parts = stem.split("_", 1)
    if len(parts) != 2:
        return None
    md5, version = parts[0], parts[1]
    if not re.fullmatch(r"[0-9a-fA-F]{8}", md5):
        return None
    return md5.lower(), version.strip()


def mhtml_extract_html_parts(mhtml_text: str) -> List[str]:
    parts: List[str] = []
    chunks = re.split(r"\n--[-_=A-Za-z0-9]+", mhtml_text)
    for ch in chunks:
        if "<html" in ch.lower() and "</html" in ch.lower():
            parts.append(ch)
    if not parts and "<html" in mhtml_text.lower():
        parts = [mhtml_text]
    return parts


def score_wave_candidate(html_text: str) -> int:
    score = 0
    for k in (
        'id="error"', "id='error'",
        'id="contrastnum"', "id='contrastnum'",
        'id="alert"', "id='alert'",
        'id="feature"', "id='feature'",
        'id="structure"', "id='structure'",
        'id="aria"', "id='aria'",
        'id="aim_score"', "id='aim_score'",
    ):
        if k in html_text:
            score += 3
    if "wave" in html_text.lower():
        score += 1
    if "webaim" in html_text.lower():
        score += 1
    return score


def extract_wave_metrics_from_html(html_text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: 0 for k in WAVE_NUM_KEYS}
    out["aim_score"] = None
    for key, pat in _WAVE_PATTERNS.items():
        m = re.search(pat, html_text, flags=re.IGNORECASE)
        if m:
            try:
                out[key] = int(m.group(1))
            except Exception:
                pass
    return out


def load_wave_from_mhtml(mhtml_path: Path) -> Dict[str, Any]:
    try:
        txt = mhtml_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    html_parts = mhtml_extract_html_parts(txt)
    if not html_parts:
        return {}

    best = max(html_parts, key=score_wave_candidate)
    metrics = extract_wave_metrics_from_html(best)

    check = [metrics.get(k, 0) for k in WAVE_NUM_KEYS]
    if all(int(v or 0) == 0 for v in check) and metrics.get("aim_score") is None:
        for p in html_parts:
            m2 = extract_wave_metrics_from_html(p)
            check2 = [m2.get(k, 0) for k in WAVE_NUM_KEYS]
            if any(int(v or 0) > 0 for v in check2) or m2.get("aim_score") is not None:
                metrics = m2
                break

    return metrics


# -------------------------
# helpers
# -------------------------
IMAGE_RELATED_RULE_IDS = {
    "image-alt",
    "image-redundant-alt",
    "color-contrast",
}


def to_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def safe_get(d: Dict[str, Any], *keys, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def sum_int(d: Dict[str, Any], keys: List[str]) -> int:
    s = 0
    for k in keys:
        try:
            s += int(d.get(k, 0) or 0)
        except Exception:
            pass
    return s


def compute_wave_problem_total(w: Dict[str, Any]) -> Optional[int]:
    if not w:
        return None
    total = 0
    anyv = False
    for k in ["Wave_Error", "Wave_Contrast_Error", "Wave_Alerts", "Wave_Aria"]:
        if k in w:
            anyv = True
            try:
                total += int(w.get(k, 0) or 0)
            except Exception:
                pass
    return total if anyv else None


def is_axe_image_related(node: Dict[str, Any]) -> bool:
    rid = (node.get("rule_id") or "").strip().lower()
    if rid in IMAGE_RELATED_RULE_IDS:
        return True
    desc = (node.get("description") or "").lower()
    help_txt = (node.get("help") or "").lower()
    html = (node.get("html") or "").lower()
    if any(s in desc for s in ("image", "alt", "контраст")):
        return True
    if any(s in help_txt for s in ("image", "alt", "contrast")):
        return True
    if "<img" in html:
        return True
    return False


# -------------------------
# build tables
# -------------------------
def build_tables(results_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame, Path]:
    json_dir = results_dir / "json"
    mhtml_dir = results_dir / "mhtml"
    out_dir = infer_analysis_dir(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wave_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if mhtml_dir.is_dir():
        for mp in sorted(mhtml_dir.glob("*.mhtml")):
            key = parse_mhtml_stem(mp.stem)
            if not key:
                continue
            wave_by_key[key] = load_wave_from_mhtml(mp)

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

        md5 = (jp.stem.split("_", 1)[0] if "_" in jp.stem else jp.stem).lower()
        version = str(meta.get("version") or "").strip()
        start_url = meta.get("start_url")

        if not isinstance(start_url, str) or not start_url.strip():
            continue

        wave_metrics = safe_get(wave, "metrics", default={}) or {}
        if not isinstance(wave_metrics, dict):
            wave_metrics = {}

        w_from_mhtml = wave_by_key.get((md5, version), {})
        if (not wave_metrics) or (meta.get("aim_score") is None and wave_metrics.get("aim_score") is None):
            if w_from_mhtml:
                merged = dict(w_from_mhtml)
                merged.update({k: v for k, v in wave_metrics.items() if v is not None})
                wave_metrics = merged

        aim_score = meta.get("aim_score")
        if aim_score is None:
            aim_score = wave_metrics.get("aim_score")

        # dom totals
        dom_total = to_int(dom.get("dom_total"))
        dom_total_visible = to_int(dom.get("dom_total_visible"))

        if dom_total_visible is None:
            elements = safe_get(data, "elements", default=[]) or []
            if isinstance(elements, list):
                visible_count = 0
                for el in elements:
                    if isinstance(el, dict) and bool(el.get("visible")):
                        visible_count += 1
                dom_total_visible = visible_count if visible_count > 0 else None

        images = to_int(dom.get("images"))
        links = to_int(dom.get("links"))
        buttons = to_int(dom.get("buttons"))
        forms = to_int(dom.get("forms"))

        images_visible = to_int(dom.get("images_visible"))
        links_visible = to_int(dom.get("links_visible"))
        buttons_visible = to_int(dom.get("buttons_visible"))
        forms_visible = to_int(dom.get("forms_visible"))

        headings_total = sum_int(dom, ["h1", "h2", "h3", "h4"])
        headings_visible = sum_int(dom, ["h1_visible", "h2_visible", "h3_visible", "h4_visible"])

        landmarks_total = sum_int(dom, ["nav", "main", "header", "footer", "section", "article"])
        landmarks_visible = sum_int(dom, ["nav_visible", "main_visible", "header_visible", "footer_visible", "section_visible", "article_visible"])

        dom_noimg_total = None
        if dom_total is not None and images is not None:
            dom_noimg_total = dom_total - images

        dom_noimg_visible = None
        if dom_total_visible is not None and images_visible is not None:
            dom_noimg_visible = dom_total_visible - images_visible

        # axe
        axe_nodes = safe_get(axe, "nodes", default=[]) or []
        if not isinstance(axe_nodes, list):
            axe_nodes = []

        axe_nodes_count = to_int(axe.get("nodes_count"))
        if axe_nodes_count is None:
            axe_nodes_count = len(axe_nodes)

        impact_summary = safe_get(axe, "impact_summary", default={}) or {}
        if not isinstance(impact_summary, dict):
            impact_summary = {}

        axe_minor = int(impact_summary.get("minor", 0) or 0)
        axe_moderate = int(impact_summary.get("moderate", 0) or 0)
        axe_serious = int(impact_summary.get("serious", 0) or 0)
        axe_critical = int(impact_summary.get("critical", 0) or 0)

        img_related = 0
        for n in axe_nodes:
            if isinstance(n, dict) and is_axe_image_related(n):
                img_related += 1

        axe_nodes_noimg = axe_nodes_count - img_related

        # manual
        manual_reason = safe_get(meta, "manual_reason", default=None)

        manual_impact = (
            safe_get(data, "axe", "manual_impact_summary", default=None)
            or safe_get(data, "manual_impact_summary", default=None)
            or safe_get(manual, "impact_summary", default=None)
        )

        manual_present = (manual_reason == 2)

        manual_ruin = None
        manual_minor = None
        manual_moderate = None
        manual_serious = None
        manual_critical = None
        manual_reassessed = None

        if isinstance(manual_impact, dict):
            manual_ruin = int(manual_impact.get("ruin", 0) or 0)
            manual_minor = int(manual_impact.get("minor", 0) or 0)
            manual_moderate = int(manual_impact.get("moderate", 0) or 0)
            manual_serious = int(manual_impact.get("serious", 0) or 0)
            manual_critical = int(manual_impact.get("critical", 0) or 0)

        if isinstance(manual, dict):
            manual_reassessed = bool(manual.get("reassessed"))

        # wave metrics
        wave_problem_total = compute_wave_problem_total(wave_metrics)
        wave_error = to_int(wave_metrics.get("Wave_Error"))
        wave_contrast = to_int(wave_metrics.get("Wave_Contrast_Error"))
        wave_alerts = to_int(wave_metrics.get("Wave_Alerts"))
        wave_aria = to_int(wave_metrics.get("Wave_Aria"))

        # ratios visible
        axe_ratio_all = (axe_nodes_count / dom_total_visible) if (dom_total_visible and axe_nodes_count is not None) else None
        axe_ratio_noimg = (axe_nodes_noimg / dom_total_visible) if (dom_total_visible and axe_nodes_noimg is not None) else None
        axe_ratio_noimg_domnoimg = (
            axe_nodes_noimg / dom_noimg_visible
            if (dom_noimg_visible and axe_nodes_noimg is not None and dom_noimg_visible > 0)
            else None
        )

        wave_ratio_all = (wave_problem_total / dom_total_visible) if (dom_total_visible and wave_problem_total is not None) else None
        wave_ratio_noimg_domnoimg = (
            wave_problem_total / dom_noimg_visible
            if (dom_noimg_visible and wave_problem_total is not None and dom_noimg_visible > 0)
            else None
        )

        # ratios total
        axe_ratio_all_total = (axe_nodes_count / dom_total) if (dom_total and axe_nodes_count is not None) else None
        axe_ratio_noimg_total = (
            axe_nodes_noimg / dom_noimg_total
            if (dom_noimg_total and axe_nodes_noimg is not None and dom_noimg_total > 0)
            else None
        )
        wave_ratio_all_total = (wave_problem_total / dom_total) if (dom_total and wave_problem_total is not None) else None
        wave_ratio_noimg_total = (
            wave_problem_total / dom_noimg_total
            if (dom_noimg_total and wave_problem_total is not None and dom_noimg_total > 0)
            else None
        )

        row = {
            "json_stem": jp.stem,
            "md5": md5,
            "start_url": start_url,
            "final_url": meta.get("final_url"),
            "version": version,
            "timestamp": meta.get("timestamp"),
            "navigation_error": data.get("navigation_error"),
            "wave_status": meta.get("wave_status"),

            "manual_reason": manual_reason,

            "aim_score": aim_score,
            "wave_problem_total": wave_problem_total,
            "Wave_Error": wave_error,
            "Wave_Contrast_Error": wave_contrast,
            "Wave_Alerts": wave_alerts,
            "Wave_Aria": wave_aria,

            "dom_total": dom_total,
            "dom_total_visible": dom_total_visible,

            "images": images,
            "links": links,
            "buttons": buttons,
            "forms": forms,

            "images_visible": images_visible,
            "links_visible": links_visible,
            "buttons_visible": buttons_visible,
            "forms_visible": forms_visible,

            "h1_visible": to_int(dom.get("h1_visible")),
            "h2_visible": to_int(dom.get("h2_visible")),
            "h3_visible": to_int(dom.get("h3_visible")),
            "h4_visible": to_int(dom.get("h4_visible")),

            "nav_visible": to_int(dom.get("nav_visible")),
            "main_visible": to_int(dom.get("main_visible")),
            "header_visible": to_int(dom.get("header_visible")),
            "footer_visible": to_int(dom.get("footer_visible")),
            "section_visible": to_int(dom.get("section_visible")),
            "article_visible": to_int(dom.get("article_visible")),

            "ul_visible": to_int(dom.get("ul_visible")),
            "ol_visible": to_int(dom.get("ol_visible")),

            "dom_noimg_total": dom_noimg_total,
            "dom_noimg_visible": dom_noimg_visible,
            "headings_total": headings_total,
            "headings_visible": headings_visible,
            "landmarks_total": landmarks_total,
            "landmarks_visible": landmarks_visible,

            "axe_nodes_count": axe_nodes_count,
            "axe_nodes_image_related": img_related,
            "axe_nodes_noimg": axe_nodes_noimg,
            "axe_minor": axe_minor,
            "axe_moderate": axe_moderate,
            "axe_serious": axe_serious,
            "axe_critical": axe_critical,

            "manual_present": int(bool(manual_present)),
            "manual_ruin": manual_ruin,
            "manual_minor": manual_minor,
            "manual_moderate": manual_moderate,
            "manual_serious": manual_serious,
            "manual_critical": manual_critical,
            "manual_reassessed": manual_reassessed,

            "axe_ratio_all": axe_ratio_all,
            "axe_ratio_noimg": axe_ratio_noimg,
            "axe_ratio_noimg_domnoimg": axe_ratio_noimg_domnoimg,
            "wave_ratio_all": wave_ratio_all,
            "wave_ratio_noimg_domnoimg": wave_ratio_noimg_domnoimg,

            "axe_ratio_all_total": axe_ratio_all_total,
            "axe_ratio_noimg_total": axe_ratio_noimg_total,
            "wave_ratio_all_total": wave_ratio_all_total,
            "wave_ratio_noimg_total": wave_ratio_noimg_total,
        }

        rows.append(row)

    raw_df = pd.DataFrame(rows)

    # --- build pairs by start_url ---
    pairs: List[Dict[str, Any]] = []
    by_start = raw_df.groupby("start_url", dropna=False)

    for start_url, g in by_start:
        if not isinstance(start_url, str) or not start_url.strip():
            continue

        normal = g[g["version"] == "normal"].head(1)
        low = g[g["version"] == "low_vision"].head(1)

        normal_present = 1 if len(normal) else 0
        low_present = 1 if len(low) else 0

        def pick(col: str, df: pd.DataFrame):
            if df.empty:
                return None
            return df.iloc[0].get(col)

        row: Dict[str, Any] = {
            "start_url": start_url,
            "normal_present": normal_present,
            "low_present": low_present,
        }

        for col in raw_df.columns:
            if col in ("start_url", "version"):
                continue
            row[f"normal_{col}"] = pick(col, normal)
            row[f"low_{col}"] = pick(col, low)

        pairs.append(row)

    paired_df = pd.DataFrame(pairs)

    raw_path = out_dir / "raw_pages.csv"
    paired_path = out_dir / "pairs.csv"
    raw_df.to_csv(raw_path, index=False, encoding="utf-8")
    paired_df.to_csv(paired_path, index=False, encoding="utf-8")

    return raw_df, paired_df, out_dir


def main():
    root = Path(__file__).resolve().parent
    results_dir = find_latest_results_dir(root)

    raw_df, paired_df, out_dir = build_tables(results_dir)

    print("OK")
    print("results:", results_dir)
    print("tables:", out_dir)
    print("raw_pages:", out_dir / "raw_pages.csv")
    print("pairs:", out_dir / "pairs.csv")
    print("raw_rows:", len(raw_df))
    print("pair_rows:", len(paired_df))


if __name__ == "__main__":
    main()