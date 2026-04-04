#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
metrics_aggregation.py

Анализ уже собранных JSON (normal vs low_vision) под H1–H4 + визуализации.
ВАЖНО: для структуры/сложности используем только *_visible.

H3:
- сравнение aim_score + axe_nodes (полный набор)
- сравнение aim_score + axe_nodes без image-related ошибок (alt/изображения/контраст изображений по эвристикам)

H4:
- scatter dom_total_visible vs aim_score
- scatter dom_total_visible vs axe_nodes

+ наглядность: low_vision "убивает" изображения:
- paired-lines / delta-hist / scatter images_visible_normal vs images_visible_low
- доля страниц, где images_visible_low == 0 при images_visible_normal > 0

Выход:
- out_dir/paired_pages.csv
- out_dir/h_tests.json
- out_dir/report.md
- out_dir/figures/*.png
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import matplotlib.pyplot as plt

# --- optional stats ---
try:
    from scipy.stats import wilcoxon, spearmanr
except Exception:
    wilcoxon = None
    spearmanr = None


# =========================
# Latest results finder
# =========================
RESULTS_RE = re.compile(r"^results_(\d{8}_\d{6})$")

def find_latest_results_json_dir(root: Path) -> Path:
    """
    Ищет root/results_YYYYMMDD_HHMMSS/json и возвращает самый свежий.
    """
    candidates: list[Tuple[str, Path]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        m = RESULTS_RE.match(d.name)
        if not m:
            continue
        json_dir = d / "json"
        if json_dir.is_dir():
            candidates.append((m.group(1), json_dir))

    if not candidates:
        raise FileNotFoundError(f"Не найдено папок вида results_*/json в: {root}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def infer_out_dir_from_json_dir(json_dir: Path) -> Path:
    """
    Кладём результаты рядом: results_.../analysis_YYYYMMDD_HHMMSS
    """
    parent = json_dir.parent  # results_YYYYMMDD_HHMMSS
    stamp = parent.name.replace("results_", "")
    return parent / f"analysis_{stamp}"


# =========================
# Safe converters (Pylance-friendly)
# =========================
from typing import Any as _Any, Optional as _Optional

def to_int(x: _Any, default: _Optional[int] = None) -> _Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default

def to_float(x: _Any, default: _Optional[float] = None) -> _Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


# =========================
# Metrics config
# =========================
IMPACT_SCORE = {"minor": 1, "moderate": 2, "serious": 3, "critical": 4}

# Список групп, которые у тебя реально считаются в dom
VISIBLE_COMPONENTS = [
    "images_visible",
    "links_visible",
    "buttons_visible",
    "forms_visible",
    "h1_visible", "h2_visible", "h3_visible", "h4_visible",
    "ul_visible", "ol_visible",
    "header_visible", "footer_visible",
    "nav_visible", "main_visible",
    "section_visible", "article_visible",
]

DOM_COLS_VISIBLE_ONLY = VISIBLE_COMPONENTS[:]  # только visible для CSV

# Для H2 используем только visible-метрики
H2_CORE = [
    ("dom_total_visible", "dom_total_visible_normal", "dom_total_visible_low"),
    ("links_visible", "links_visible_normal", "links_visible_low"),
    ("buttons_visible", "buttons_visible_normal", "buttons_visible_low"),
    ("forms_visible", "forms_visible_normal", "forms_visible_low"),
    ("headings_visible", "headings_visible_normal", "headings_visible_low"),
    ("landmarks_visible", "landmarks_visible_normal", "landmarks_visible_low"),
]

# Какие rule_id считаем "image-related" (можно расширять)
IMAGE_RULE_IDS = {
    "image-alt",
    "image-redundant-alt",
    "input-image-alt",
    "object-alt",
    "svg-img-alt",
    "area-alt",
}

def is_image_related_axe_node(node: Dict[str, Any]) -> bool:
    """
    Эвристика: считаем node "image-related", если:
    - rule_id явно из IMAGE_RULE_IDS
    - rule_id содержит 'image' или 'img'
    - html/target указывает на img/svg/picture
    - rule_id == 'color-contrast' и похоже, что это именно изображение (background-image/img)
      (это приблизительно, но покрывает запрос "контраст изображения" как явление)
    """
    rid = node.get("rule_id")
    rid_s = rid.lower().strip() if isinstance(rid, str) else ""

    if rid_s in IMAGE_RULE_IDS:
        return True
    if "image" in rid_s or "img" in rid_s:
        return True

    html = node.get("html")
    html_s = html.lower() if isinstance(html, str) else ""

    # targets часто list; превращаем в строку для поиска
    tgt = node.get("targets")
    tgt_s = ""
    try:
        if isinstance(tgt, list):
            tgt_s = " ".join(str(x) for x in tgt).lower()
        elif isinstance(tgt, str):
            tgt_s = tgt.lower()
    except Exception:
        tgt_s = ""

    if "<img" in html_s or "<picture" in html_s or "<svg" in html_s:
        return True
    if "img" in tgt_s or "svg" in tgt_s or "picture" in tgt_s:
        return True

    # "контраст изображения" — грубо:
    if rid_s == "color-contrast":
        if "background-image" in html_s or "<img" in html_s or "url(" in html_s:
            return True

    return False


# =========================
# Data model
# =========================
@dataclass
class PageRec:
    start_url: str
    final_url: Optional[str]
    version: str
    timestamp: Optional[str]

    dom: Dict[str, Any]

    axe_nodes_count: int
    axe_violations_count: int
    axe_impact_summary: Dict[str, int]
    axe_nodes: List[Dict[str, Any]]  # raw nodes

    aim_score: Optional[float]

    navigation_error: Optional[str]


# =========================
# JSON parsing helpers
# =========================
def safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def infer_version(data: Dict[str, Any], filename: str) -> str:
    v = safe_get(data, ["meta", "version"], None)
    if isinstance(v, str) and v.strip():
        return v.strip()
    lower = filename.lower()
    if "low_vision" in lower or "_low" in lower or "_lv" in lower:
        return "low_vision"
    if "normal" in lower or "main" in lower:
        return "normal"
    return "unknown"

def parse_page(data: Dict[str, Any], filename: str) -> Optional[PageRec]:
    start_url = safe_get(data, ["meta", "start_url"], None)
    if not isinstance(start_url, str) or not start_url.strip():
        return None

    version = infer_version(data, filename)

    axe_nodes = safe_get(data, ["axe", "nodes"], []) or []
    if not isinstance(axe_nodes, list):
        axe_nodes = []

    aim = safe_get(data, ["meta", "aim_score"], None)
    aim_score = to_float(aim, default=None)

    return PageRec(
        start_url=start_url.strip(),
        final_url=safe_get(data, ["meta", "final_url"], None),
        version=version,
        timestamp=safe_get(data, ["meta", "timestamp"], None),
        dom=safe_get(data, ["dom"], {}) or {},

        axe_nodes_count=to_int(safe_get(data, ["axe", "nodes_count"], None), default=0) or 0,
        axe_violations_count=to_int(safe_get(data, ["axe", "violations_count"], None), default=0) or 0,
        axe_impact_summary=safe_get(data, ["axe", "impact_summary"], {}) or {},
        axe_nodes=axe_nodes,

        aim_score=aim_score,
        navigation_error=safe_get(data, ["navigation_error"], None),
    )


# =========================
# Derived metrics (VISIBLE ONLY for structure)
# =========================
def dom_visible_int(page: Optional[PageRec], key: str) -> Optional[int]:
    if not page:
        return None
    return to_int(page.dom.get(key), default=None)

def dom_total_visible(page: Optional[PageRec]) -> Optional[int]:
    if not page:
        return None
    total = 0
    for k in VISIBLE_COMPONENTS:
        total += (to_int(page.dom.get(k), default=0) or 0)
    return total

def headings_visible(page: Optional[PageRec]) -> Optional[int]:
    if not page:
        return None
    return sum((to_int(page.dom.get(k), default=0) or 0) for k in ["h1_visible", "h2_visible", "h3_visible", "h4_visible"])

def landmarks_visible(page: Optional[PageRec]) -> Optional[int]:
    if not page:
        return None
    return sum((to_int(page.dom.get(k), default=0) or 0) for k in ["nav_visible","main_visible","header_visible","footer_visible","section_visible","article_visible"])

def impact_mean(summary: Dict[str, int]) -> Optional[float]:
    total = 0
    s = 0
    for k in ["minor", "moderate", "serious", "critical"]:
        c = to_int(summary.get(k), default=0) or 0
        if c > 0:
            total += c
            s += IMPACT_SCORE[k] * c
    return (s / total) if total else None

def axe_nodes_count_no_images(nodes: List[Dict[str, Any]]) -> int:
    """
    Считает количество axe nodes после выкидывания image-related проблем.
    """
    kept = 0
    for n in nodes:
        # n содержит: rule_id, impact, html, targets и т.д.
        # мы приводим к "формату node", который ожидает is_image_related_axe_node()
        node = {
            "rule_id": n.get("rule_id"),
            "html": n.get("html"),
            "targets": n.get("targets") or n.get("target"),
        }
        if is_image_related_axe_node(node):
            continue
        kept += 1
    return kept


# =========================
# Stats
# =========================
def paired_wilcoxon(x: List[Optional[float]], y: List[Optional[float]], alternative: str) -> Dict[str, Any]:
    """
    Wilcoxon для парных наблюдений.
    alternative:
      - "greater": x > y
      - "less":    x < y
    """
    out: Dict[str, Any] = {"n": 0, "test": "wilcoxon", "alternative": alternative, "p_value": None, "statistic": None}

    pairs = []
    for a, b in zip(x, y):
        if a is None or b is None:
            continue
        if isinstance(a, float) and math.isnan(a):
            continue
        if isinstance(b, float) and math.isnan(b):
            continue
        pairs.append((float(a), float(b)))

    out["n"] = len(pairs)
    if len(pairs) < 5:
        out["note"] = "too_few_pairs"
        return out
    if wilcoxon is None:
        out["note"] = "scipy_not_available"
        return out

    a_list = [p[0] for p in pairs]
    b_list = [p[1] for p in pairs]

    try:
        res = wilcoxon(a_list, b_list, alternative=alternative, zero_method="wilcox")
        out["p_value"] = to_float(getattr(res, "pvalue", None))
        out["statistic"] = to_float(getattr(res, "statistic", None))
    except TypeError:
        res = wilcoxon(a_list, b_list)
        out["p_value"] = to_float(getattr(res, "pvalue", None))
        out["statistic"] = to_float(getattr(res, "statistic", None))
        out["note"] = "wilcoxon_without_alternative"
    except Exception as e:
        out["note"] = f"wilcoxon_error: {e}"

    return out

def spearman_test(x: List[Optional[float]], y: List[Optional[float]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": 0, "test": "spearmanr", "rho": None, "p_value": None}
    pairs = []
    for a, b in zip(x, y):
        if a is None or b is None:
            continue
        if isinstance(a, float) and math.isnan(a):
            continue
        if isinstance(b, float) and math.isnan(b):
            continue
        pairs.append((float(a), float(b)))

    out["n"] = len(pairs)
    if len(pairs) < 8:
        out["note"] = "too_few_pairs"
        return out
    if spearmanr is None:
        out["note"] = "scipy_not_available"
        return out

    a_list = [p[0] for p in pairs]
    b_list = [p[1] for p in pairs]

    try:
        rho, p = spearmanr(a_list, b_list)
        out["rho"] = to_float(rho)
        out["p_value"] = to_float(p)
    except Exception as e:
        out["note"] = f"spearman_error: {e}"

    return out


# =========================
# Plot helpers
# =========================
def ensure_figdir(out_dir: Path) -> Path:
    figdir = out_dir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    return figdir

def savefig(figdir: Path, name: str) -> None:
    plt.tight_layout()
    plt.savefig(figdir / name, dpi=160)
    plt.close()

def series_pairs(df: pd.DataFrame, col_a: str, col_b: str) -> Tuple[List[float], List[float]]:
    sub = df[[col_a, col_b]].dropna()
    if sub.empty:
        return ([], [])
    a = sub[col_a].astype(float).tolist()
    b = sub[col_b].astype(float).tolist()
    return a, b

def plot_paired_lines(df: pd.DataFrame, a_col: str, b_col: str, title: str, figdir: Path, fname: str, max_lines: int = 140) -> None:
    a, b = series_pairs(df, a_col, b_col)
    if not a:
        return
    n = len(a)
    if n > max_lines:
        step = max(1, n // max_lines)
        a = a[::step]
        b = b[::step]

    plt.figure()
    for i in range(len(a)):
        plt.plot([0, 1], [a[i], b[i]], marker="o")
    plt.xticks([0, 1], ["normal", "low_vision"])
    plt.title(title)
    plt.ylabel(a_col.replace("_normal", "").replace("_low", ""))
    savefig(figdir, fname)

def plot_delta_hist(df: pd.DataFrame, a_col: str, b_col: str, title: str, figdir: Path, fname: str) -> None:
    a, b = series_pairs(df, a_col, b_col)
    if not a:
        return
    delta = [bb - aa for aa, bb in zip(a, b)]
    plt.figure()
    plt.hist(delta, bins=22)
    plt.axvline(0)
    plt.title(title)
    plt.xlabel("Δ = low_vision - normal")
    plt.ylabel("count")
    savefig(figdir, fname)

def plot_scatter(df: pd.DataFrame, xcol: str, ycol: str, title: str, figdir: Path, fname: str) -> None:
    sub = df[[xcol, ycol]].dropna()
    if sub.empty:
        return
    x = sub[xcol].astype(float).tolist()
    y = sub[ycol].astype(float).tolist()

    plt.figure()
    plt.scatter(x, y)
    plt.xlabel(xcol)
    plt.ylabel(ycol)
    plt.title(title)

    # тренд (простая линия)
    if len(x) >= 2:
        try:
            import numpy as np
            coef = np.polyfit(x, y, 1)
            xs = np.linspace(min(x), max(x), 100)
            ys = coef[0] * xs + coef[1]
            plt.plot(xs, ys)
        except Exception:
            pass

    savefig(figdir, fname)


# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Где искать results_*/json (по умолчанию текущая папка проекта)")
    ap.add_argument("--json_dir", default=None, help="Явно указать json_dir (если не хочешь auto-find)")
    ap.add_argument("--out_dir", default=None, help="Куда писать результаты (по умолчанию рядом с latest results_*)")
    ap.add_argument("--normal_name", default="normal", help="Название normal версии в meta.version")
    ap.add_argument("--low_name", default="low_vision", help="Название low_vision версии в meta.version")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    if args.json_dir:
        json_dir = Path(args.json_dir).resolve()
    else:
        json_dir = find_latest_results_json_dir(root)

    if not json_dir.is_dir():
        raise SystemExit(f"json_dir не найден: {json_dir}")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else infer_out_dir_from_json_dir(json_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    figdir = ensure_figdir(out_dir)

    # -------- read pages
    pages: List[PageRec] = []
    for f in sorted(json_dir.glob("*.json")):
        data = load_json(f)
        if not data:
            continue
        rec = parse_page(data, f.name)
        if rec:
            pages.append(rec)

    if not pages:
        raise SystemExit(f"В {json_dir} нет валидных JSON.")

    # -------- index by start_url + version
    by_start: Dict[str, Dict[str, PageRec]] = {}
    for p in pages:
        by_start.setdefault(p.start_url, {})
        by_start[p.start_url][p.version] = p

    # -------- build paired rows
    rows: List[Dict[str, Any]] = []
    for start_url, versions in by_start.items():
        normal = versions.get(args.normal_name) or versions.get("main") or versions.get("normal")
        low = versions.get(args.low_name) or versions.get("lowvision") or versions.get("low_vision")

        row: Dict[str, Any] = {
            "start_url": start_url,
            "has_normal": normal is not None,
            "has_low_vision": low is not None,
            "navigation_error_normal": normal.navigation_error if normal else None,
            "navigation_error_low": low.navigation_error if low else None,
            "final_url_normal": normal.final_url if normal else None,
            "final_url_low": low.final_url if low else None,
            "low_vision_method": (
                "inplace_toggle" if (normal and low and str(normal.final_url) == str(low.final_url))
                else ("separate_site_or_navigation" if (normal and low) else None)
            ),
        }

        # --- aim_score
        row["aim_score_normal"] = normal.aim_score if normal else None
        row["aim_score_low"] = low.aim_score if low else None

        # --- axe
        row["axe_nodes_normal"] = normal.axe_nodes_count if normal else None
        row["axe_nodes_low"] = low.axe_nodes_count if low else None

        row["axe_nodes_no_images_normal"] = axe_nodes_count_no_images(normal.axe_nodes) if normal else None
        row["axe_nodes_no_images_low"] = axe_nodes_count_no_images(low.axe_nodes) if low else None

        # --- visible structure derived
        row["dom_total_visible_normal"] = dom_total_visible(normal)
        row["dom_total_visible_low"] = dom_total_visible(low)

        # alias: "dom_total" трактуем как dom_total_visible (только visible) — по твоему правилу
        row["dom_total_normal"] = row["dom_total_visible_normal"]
        row["dom_total_low"] = row["dom_total_visible_low"]

        row["headings_visible_normal"] = headings_visible(normal)
        row["headings_visible_low"] = headings_visible(low)
        row["landmarks_visible_normal"] = landmarks_visible(normal)
        row["landmarks_visible_low"] = landmarks_visible(low)

        # --- visible groups in CSV
        for c in DOM_COLS_VISIBLE_ONLY:
            row[f"{c}_normal"] = dom_visible_int(normal, c)
            row[f"{c}_low"] = dom_visible_int(low, c)

        rows.append(row)

    df = pd.DataFrame(rows)
    (out_dir / "paired_pages.csv").write_text(df.to_csv(index=False), encoding="utf-8")

    # -------- paired OK only
    paired = df[(df["has_normal"] == True) & (df["has_low_vision"] == True)].copy()
    paired_ok = paired[(paired["navigation_error_normal"].isna()) & (paired["navigation_error_low"].isna())].copy()

    # =========================
    # Tests
    # =========================
    tests: Dict[str, Any] = {
        "meta": {
            "json_dir": str(json_dir),
            "out_dir": str(out_dir),
            "total_json_parsed": len(pages),
            "unique_start_urls": int(df["start_url"].nunique()),
            "pairs_total": int(len(paired)),
            "pairs_ok": int(len(paired_ok)),
            "note": "STRUCTURE METRICS USE *_visible ONLY. dom_total is aliased to dom_total_visible.",
        }
    }

    # --- H2 (structure visible only): normal > low
    h2: Dict[str, Any] = {}
    for name, a_col, b_col in H2_CORE:
        x = [to_float(v) for v in paired_ok[a_col].tolist()]
        y = [to_float(v) for v in paired_ok[b_col].tolist()]
        h2[name] = paired_wilcoxon(x, y, alternative="greater")
    tests["H2_visible_structure"] = h2

    # --- H3: aim_score + axe_nodes
    # aim_score: higher is better => expect low > normal => test (normal < low) => alternative="less" for (normal, low)
    tests["H3"] = {
        "full": {
            "aim_score_low_gt_normal": paired_wilcoxon(
                [to_float(v) for v in paired_ok["aim_score_normal"].tolist()],
                [to_float(v) for v in paired_ok["aim_score_low"].tolist()],
                alternative="less",
            ),
            "axe_nodes_normal_gt_low": paired_wilcoxon(
                [to_float(v) for v in paired_ok["axe_nodes_normal"].tolist()],
                [to_float(v) for v in paired_ok["axe_nodes_low"].tolist()],
                alternative="greater",
            ),
        },
        "no_image_errors": {
            "aim_score_low_gt_normal": paired_wilcoxon(
                [to_float(v) for v in paired_ok["aim_score_normal"].tolist()],
                [to_float(v) for v in paired_ok["aim_score_low"].tolist()],
                alternative="less",
            ),
            "axe_nodes_no_images_normal_gt_low": paired_wilcoxon(
                [to_float(v) for v in paired_ok["axe_nodes_no_images_normal"].tolist()],
                [to_float(v) for v in paired_ok["axe_nodes_no_images_low"].tolist()],
                alternative="greater",
            ),
            "image_filter_note": "axe_nodes_no_images uses heuristics over rule_id/html/target; extend IMAGE_RULE_IDS if needed.",
        },
    }

    # --- H4: complexity vs errors/scores (visible only)
    tests["H4"] = {
        "dom_total_visible_vs_axe_nodes_normal": spearman_test(
            [to_float(v) for v in paired_ok["dom_total_visible_normal"].tolist()],
            [to_float(v) for v in paired_ok["axe_nodes_normal"].tolist()],
        ),
        "dom_total_visible_vs_axe_nodes_low": spearman_test(
            [to_float(v) for v in paired_ok["dom_total_visible_low"].tolist()],
            [to_float(v) for v in paired_ok["axe_nodes_low"].tolist()],
        ),
        "dom_total_visible_vs_aim_score_normal": spearman_test(
            [to_float(v) for v in paired_ok["dom_total_visible_normal"].tolist()],
            [to_float(v) for v in paired_ok["aim_score_normal"].tolist()],
        ),
        "dom_total_visible_vs_aim_score_low": spearman_test(
            [to_float(v) for v in paired_ok["dom_total_visible_low"].tolist()],
            [to_float(v) for v in paired_ok["aim_score_low"].tolist()],
        ),
    }

    # --- Images removal evidence
    img_norm = pd.to_numeric(paired_ok["images_visible_normal"], errors="coerce")
    img_low = pd.to_numeric(paired_ok["images_visible_low"], errors="coerce")

    removed_mask = (img_norm > 0) & (img_low == 0)
    share_removed = float(removed_mask.mean()) if len(paired_ok) else None

    tests["images_removal"] = {
        "share_pages_where_images_removed": share_removed,
        "n_pairs_ok": int(len(paired_ok)),
        "definition": "images_visible_normal > 0 and images_visible_low == 0",
        "wilcoxon_images_normal_gt_low": paired_wilcoxon(
            [to_float(v) for v in paired_ok["images_visible_normal"].tolist()],
            [to_float(v) for v in paired_ok["images_visible_low"].tolist()],
            alternative="greater",
        ),
    }

    (out_dir / "h_tests.json").write_text(json.dumps(tests, ensure_ascii=False, indent=2), encoding="utf-8")

    # =========================
    # Visualizations
    # =========================
    plot_df = paired_ok.copy()

    # H2 plots
    for name, a, b in H2_CORE:
        plot_paired_lines(plot_df, a, b, f"H2 (visible): {name} (normal → low)", figdir, f"H2_paired_{name}.png")
        plot_delta_hist(plot_df, a, b, f"H2 (visible): Δ {name} (low - normal)", figdir, f"H2_hist_delta_{name}.png")

    # H3 plots: axe_nodes full and no-image
    plot_paired_lines(plot_df, "axe_nodes_normal", "axe_nodes_low", "H3: axe_nodes (normal → low)", figdir, "H3_paired_axe_nodes.png")
    plot_delta_hist(plot_df, "axe_nodes_normal", "axe_nodes_low", "H3: Δ axe_nodes (low - normal)", figdir, "H3_hist_delta_axe_nodes.png")

    plot_paired_lines(plot_df, "axe_nodes_no_images_normal", "axe_nodes_no_images_low", "H3: axe_nodes (no image errors) (normal → low)", figdir, "H3_paired_axe_nodes_no_images.png")
    plot_delta_hist(plot_df, "axe_nodes_no_images_normal", "axe_nodes_no_images_low", "H3: Δ axe_nodes_no_images (low - normal)", figdir, "H3_hist_delta_axe_nodes_no_images.png")

    # H3: aim_score (higher better) — paired
    plot_paired_lines(plot_df, "aim_score_normal", "aim_score_low", "H3: aim_score (normal → low)", figdir, "H3_paired_aim_score.png")
    plot_delta_hist(plot_df, "aim_score_normal", "aim_score_low", "H3: Δ aim_score (low - normal)", figdir, "H3_hist_delta_aim_score.png")

    # Images removal visuals
    plot_paired_lines(plot_df, "images_visible_normal", "images_visible_low", "Images visible (normal → low)", figdir, "IMG_paired_images_visible.png")
    plot_delta_hist(plot_df, "images_visible_normal", "images_visible_low", "Δ images_visible (low - normal)", figdir, "IMG_hist_delta_images_visible.png")
    plot_scatter(plot_df, "images_visible_normal", "images_visible_low",
                 "Images visible: normal vs low_vision", figdir, "IMG_scatter_images_visible_normal_vs_low.png")

    # H4 scatter required
    # 1) dom_total (aliased visible) + aim_score
    plot_scatter(plot_df, "dom_total_normal", "aim_score_normal",
                 "H4: dom_total(visible) vs aim_score (normal)", figdir,
                 "H4_scatter_dom_total_vs_aim_score_normal.png")
    plot_scatter(plot_df, "dom_total_low", "aim_score_low",
                 "H4: dom_total(visible) vs aim_score (low_vision)", figdir,
                 "H4_scatter_dom_total_vs_aim_score_low.png")

    # 2) dom_total_visible + axe_nodes
    plot_scatter(plot_df, "dom_total_visible_normal", "axe_nodes_normal",
                 "H4: dom_total_visible vs axe_nodes (normal)", figdir,
                 "H4_scatter_dom_total_visible_vs_axe_nodes_normal.png")
    plot_scatter(plot_df, "dom_total_visible_low", "axe_nodes_low",
                 "H4: dom_total_visible vs axe_nodes (low_vision)", figdir,
                 "H4_scatter_dom_total_visible_vs_axe_nodes_low.png")

    # =========================
    # Report (fast Markdown)
    # =========================
    def fmt(p):
        if p is None:
            return "NA"
        try:
            return f"{float(p):.4g}"
        except Exception:
            return str(p)

    lines: List[str] = []
    lines.append("# Отчёт H1–H4 (visible-only структура; H3 aim_score+axe)\n\n")
    lines.append(f"- json_dir: `{json_dir}`\n")
    lines.append(f"- out_dir: `{out_dir}`\n")
    lines.append(f"- пары (ok): **{int(len(paired_ok))}**\n\n")

    lines.append("## Важно про метрики структуры\n\n")
    lines.append("- Все структурные метрики считаются **только по видимым элементам** (`*_visible`).\n")
    lines.append("- `dom_total_visible` — сумма видимых элементов по заданным семантическим группам.\n")
    lines.append("- `dom_total` в графиках/таблицах — это **алиас dom_total_visible** (чтобы сохранить запрос на график dom_total + aim_score, но не нарушать правило visible-only).\n\n")

    # H2
    lines.append("## H2 (visible): структура normal > low_vision\n\n")
    lines.append("| метрика | n | p-value |\n|---|---:|---:|\n")
    for k, r in tests["H2_visible_structure"].items():
        lines.append(f"| {k} | {r.get('n',0)} | {fmt(r.get('p_value'))} |\n")
    lines.append("\n")

    # H3
    lines.append("## H3: aim_score + axe_nodes (2 варианта)\n\n")
    lines.append("### H3-A: полный набор ошибок\n\n")
    r1 = tests["H3"]["full"]["aim_score_low_gt_normal"]
    r2 = tests["H3"]["full"]["axe_nodes_normal_gt_low"]
    lines.append(f"- aim_score (low > normal): n={r1.get('n')} p={fmt(r1.get('p_value'))}\n")
    lines.append(f"- axe_nodes (normal > low): n={r2.get('n')} p={fmt(r2.get('p_value'))}\n\n")

    lines.append("### H3-B: без ошибок, связанных с изображениями (axe)\n\n")
    r3 = tests["H3"]["no_image_errors"]["axe_nodes_no_images_normal_gt_low"]
    lines.append(f"- axe_nodes_no_images (normal > low): n={r3.get('n')} p={fmt(r3.get('p_value'))}\n")
    lines.append(f"- примечание: {tests['H3']['no_image_errors']['image_filter_note']}\n\n")

    # Images removal
    lines.append("## Наглядность: low_vision удаляет изображения\n\n")
    ir = tests["images_removal"]
    lines.append(f"- доля страниц, где `images_visible_normal > 0` и `images_visible_low == 0`: **{fmt(ir.get('share_pages_where_images_removed'))}**\n")
    rimg = ir["wilcoxon_images_normal_gt_low"]
    lines.append(f"- Wilcoxon images_visible (normal > low): n={rimg.get('n')} p={fmt(rimg.get('p_value'))}\n\n")

    # H4
    lines.append("## H4: связь видимой сложности с ошибками и aim_score\n\n")
    for k, r in tests["H4"].items():
        lines.append(f"- {k}: n={r.get('n')} rho={fmt(r.get('rho'))} p={fmt(r.get('p_value'))} note={r.get('note','')}\n")
    lines.append("\n")

    lines.append("## Графики\n\n")
    lines.append(f"PNG лежат в `{out_dir / 'figures'}`\n")

    (out_dir / "report.md").write_text("".join(lines), encoding="utf-8")

    print("OK")
    print("Latest json_dir:", json_dir)
    print("Saved out_dir:", out_dir)
    print("Figures:", out_dir / "figures")


if __name__ == "__main__":
    main()