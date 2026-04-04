#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import pandas as pd

try:
    from scipy.stats import spearmanr, wilcoxon
except Exception:
    spearmanr = None
    wilcoxon = None

try:
    from statsmodels.stats.power import TTestPower
except Exception:
    TTestPower = None


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
        if (d / "tables").is_dir():
            candidates.append((m.group(1), d))

    if not candidates:
        raise FileNotFoundError(f"Не найдено results_*/tables в: {root}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def fmt_num(v: Any) -> str:
    try:
        v=float(v)
        if abs(v) < 1e-4:
            return f"{v:.3e}"
        return f"{v:.6f}"
    except Exception:
        return "NA"


def severity_mean(minor: Any, moderate: Any, serious: Any, critical: Any, ruin: Any = 0) -> float:
    vals = [minor, moderate, serious, critical, ruin]

    if all(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return math.nan

    def _num(v: Any) -> float:
        if v is None:
            return 0.0
        try:
            fv = float(v)
            if math.isnan(fv):
                return 0.0
            return fv
        except Exception:
            return 0.0

    minor_f = _num(minor)
    moderate_f = _num(moderate)
    serious_f = _num(serious)
    critical_f = _num(critical)
    ruin_f = _num(ruin)

    total = minor_f + moderate_f + serious_f + critical_f + ruin_f
    if total <= 0:
        return math.nan

    weighted = 1 * minor_f + 2 * moderate_f + 3 * serious_f + 4 * critical_f + 5 * ruin_f
    return weighted / total


# ------------------------------------------------------------
# statistical tests
# ------------------------------------------------------------

def wilcoxon_test(x: List[float], y: List[float], alternative: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "test": "wilcoxon",
        "alternative": alternative,
        "n": 0,
        "statistic": None,
        "p_value": None,
        "median_delta": None,
        "note": None,
    }

    df = pd.DataFrame({"x": x, "y": y}).dropna()
    out["n"] = int(len(df))

    if len(df) < 8:
        out["note"] = "too_few_pairs"
        return out

    if wilcoxon is None:
        out["note"] = "scipy_not_available"
        return out

    try:
        res = wilcoxon(df["x"], df["y"], alternative=alternative, zero_method="wilcox")
        out["statistic"] = float(getattr(res, "statistic", res[0]))
        out["p_value"] = float(getattr(res, "pvalue", res[1]))
        out["median_delta"] = float((df["x"] - df["y"]).median())
    except Exception as e:
        out["note"] = f"wilcoxon_error: {e}"

    return out


def spearman_test(x: List[float], y: List[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "test": "spearmanr",
        "n": 0,
        "rho": None,
        "p_value": None,
        "note": None,
    }

    df = pd.DataFrame({"x": x, "y": y}).dropna()
    out["n"] = int(len(df))

    if len(df) < 10:
        out["note"] = "too_few_pairs"
        return out

    if spearmanr is None:
        out["note"] = "scipy_not_available"
        return out

    try:
        rho, p = spearmanr(df["x"], df["y"])
        out["rho"] = float(rho)
        out["p_value"] = float(p)
    except Exception as e:
        out["note"] = f"spearman_error: {e}"

    return out


def paired_effect_and_power(
    x: List[float],
    y: List[float],
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "n": 0,
        "mean_diff": None,
        "sd_diff": None,
        "dz": None,
        "power_approx": None,
        "note": None,
    }

    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < 3:
        out["note"] = "too_few_pairs"
        return out

    d = df["x"].astype(float) - df["y"].astype(float)
    n = int(len(d))
    mean_diff = float(d.mean())
    sd_diff = float(d.std(ddof=1))

    out["n"] = n
    out["mean_diff"] = mean_diff
    out["sd_diff"] = sd_diff

    if sd_diff == 0:
        out["note"] = "zero_sd_diff"
        return out

    dz = mean_diff / sd_diff
    out["dz"] = dz

    if TTestPower is None:
        out["note"] = "statsmodels_not_available"
        return out

    alt = alternative
    if alt == "greater":
        alt = "larger"
    elif alt == "less":
        alt = "smaller"

    try:
        pwr = TTestPower().power(
            effect_size=abs(dz),
            nobs=n,
            alpha=alpha,
            alternative=alt,
        )
        out["power_approx"] = float(pwr)
    except Exception as e:
        out["note"] = f"power_error: {e}"

    return out


def spearman_power_approx(
    x: List[float],
    y: List[float],
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "n": 0,
        "rho": None,
        "power_approx": None,
        "note": None,
    }

    df = pd.DataFrame({"x": x, "y": y}).dropna()
    n = int(len(df))
    out["n"] = n

    if n < 4:
        out["note"] = "too_few_pairs"
        return out

    if spearmanr is None:
        out["note"] = "scipy_not_available"
        return out

    try:
        rho, _ = spearmanr(df["x"], df["y"])
        rho = float(rho)
        out["rho"] = rho

        if abs(rho) >= 1:
            out["power_approx"] = 1.0
            return out

        delta = 0.5 * math.log((1 + rho) / (1 - rho)) * math.sqrt(n - 3)
        nd = NormalDist()

        if alternative == "two-sided":
            zcrit = nd.inv_cdf(1 - alpha / 2)
            power = 1 - nd.cdf(zcrit - abs(delta)) + nd.cdf(-zcrit - abs(delta))
        elif alternative == "greater":
            zcrit = nd.inv_cdf(1 - alpha)
            power = 1 - nd.cdf(zcrit - delta)
        else:
            zcrit = nd.inv_cdf(1 - alpha)
            power = nd.cdf(-zcrit - delta)

        out["power_approx"] = float(power)
    except Exception as e:
        out["note"] = f"spearman_power_error: {e}"

    return out


# ------------------------------------------------------------
# plots
# ------------------------------------------------------------

def savefig(figdir: Path, name: str) -> None:
    plt.tight_layout()
    plt.savefig(figdir / name, dpi=160)
    plt.close()


def plot_paired_delta_hist(
    df: pd.DataFrame,
    xcol: str,
    ycol: str,
    title: str,
    figdir: Path,
    fname: str,
) -> None:
    sub = df[[xcol, ycol]].dropna()
    if sub.empty:
        return

    delta = (sub[xcol].astype(float) - sub[ycol].astype(float)).tolist()

    plt.figure()
    plt.hist(delta, bins=20)
    plt.axvline(0)
    plt.title(title)
    plt.xlabel("Δ = x - y")
    plt.ylabel("count")
    savefig(figdir, fname)


def plot_scatter(
    df: pd.DataFrame,
    xcol: str,
    ycol: str,
    title: str,
    figdir: Path,
    fname: str,
) -> None:
    sub = df[[xcol, ycol]].dropna()
    if sub.empty:
        return

    plt.figure()
    plt.scatter(sub[xcol].astype(float).tolist(), sub[ycol].astype(float).tolist())
    plt.xlabel(xcol)
    plt.ylabel(ycol)
    plt.title(title)
    savefig(figdir, fname)


def plot_dom_vs_aim(df: pd.DataFrame, figdir: Path) -> pd.DataFrame:
    sub = df[["dom_total", "aim_score"]].dropna().copy()

    if sub.empty:
        return sub

    x = sub["dom_total"].astype(float).tolist()
    y = sub["aim_score"].astype(float).tolist()

    plt.figure()
    plt.scatter(x, y)
    plt.xlabel("dom_total")
    plt.ylabel("aim_score")
    plt.title("DOM size vs AIM score")

    savefig(figdir, "H4_scatter_dom_total_vs_aim_score.png")
    return sub


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def main() -> None:
    results_dir = find_latest_results_dir(ROOT)
    in_dir = results_dir / "tables"

    raw_path = in_dir / "raw_pages.csv"
    pairs_path = in_dir / "pairs.csv"

    if not raw_path.exists():
        raise FileNotFoundError(f"Не найден raw_pages.csv: {raw_path}")
    if not pairs_path.exists():
        raise FileNotFoundError(f"Не найден pairs.csv: {pairs_path}")

    raw = pd.read_csv(raw_path)
    pairs = pd.read_csv(pairs_path)

    out_dir = in_dir
    figdir = ensure_dir(out_dir / "figures")

    # --------------------------------------------------------
    # совместимость с разными версиями pairs.csv
    # --------------------------------------------------------
    if "normal_present" not in pairs.columns and "has_normal" in pairs.columns:
        pairs["normal_present"] = pairs["has_normal"].astype(int)

    if "low_present" not in pairs.columns and "has_low" in pairs.columns:
        pairs["low_present"] = pairs["has_low"].astype(int)

    renamed_cols: Dict[str, str] = {}
    for col in list(pairs.columns):
        if col.endswith("_normal"):
            base = col[:-7]
            new_col = f"normal_{base}"
            if new_col not in pairs.columns:
                renamed_cols[col] = new_col
        elif col.endswith("_low"):
            base = col[:-4]
            new_col = f"low_{base}"
            if new_col not in pairs.columns:
                renamed_cols[col] = new_col

    if renamed_cols:
        pairs = pairs.rename(columns=renamed_cols)

    # --------------------------------------------------------
    # фильтр валидных пар
    # --------------------------------------------------------
    pairs_ok = pairs[
        (pairs["normal_present"] == 1)
        & (pairs["low_present"] == 1)
        & (pairs["normal_navigation_error"].isna() | (pairs["normal_navigation_error"] == ""))
        & (pairs["low_navigation_error"].isna() | (pairs["low_navigation_error"] == ""))
    ].copy()

    tests: Dict[str, Any] = {
        "meta": {
            "results_dir": str(results_dir),
            "tables_dir": str(in_dir),
            "out_dir": str(out_dir),
            "pairs_ok": int(len(pairs_ok)),
            "raw_rows": int(len(raw)),
        }
    }

    # ========================================================
    # H1: manual vs axe severity on normal pages
    # ========================================================
    raw_norm = raw[
        (raw["version"] == "normal")
        & (raw["navigation_error"].isna() | (raw["navigation_error"] == ""))
    ].copy()

    has_manual_cols = {
        "manual_minor",
        "manual_moderate",
        "manual_serious",
        "manual_critical",
    }.issubset(set(raw_norm.columns))

    has_axe_cols = {
        "axe_minor",
        "axe_moderate",
        "axe_serious",
        "axe_critical",
    }.issubset(set(raw_norm.columns))

    if (
        has_manual_cols
        and has_axe_cols
        and ("manual_present" in raw_norm.columns)
        and (raw_norm["manual_present"].fillna(False).astype(bool).sum() >= 5)
    ):
        raw_norm["sev_axe"] = raw_norm.apply(
            lambda r: severity_mean(
                r.get("axe_minor"),
                r.get("axe_moderate"),
                r.get("axe_serious"),
                r.get("axe_critical"),
                0,
            ),
            axis=1,
        )

        raw_norm["sev_manual"] = raw_norm.apply(
            lambda r: severity_mean(
                r.get("manual_minor"),
                r.get("manual_moderate"),
                r.get("manual_serious"),
                r.get("manual_critical"),
                r.get("manual_ruin", 0),
            ),
            axis=1,
        )

        labeled = raw_norm[raw_norm["manual_present"].fillna(False).astype(bool)].copy()

        tests["H1"] = {
            "n_pages_labeled": int(len(labeled)),
            "wilcoxon_manual_gt_axe": wilcoxon_test(
                labeled["sev_axe"].astype(float).tolist(),
                labeled["sev_manual"].astype(float).tolist(),
                alternative="less",
            ),
            "power_approx": paired_effect_and_power(
                labeled["sev_axe"].astype(float).tolist(),
                labeled["sev_manual"].astype(float).tolist(),
                alpha=0.05,
                alternative="less",
            ),
        }

        plt.figure()
        plt.boxplot(
            [labeled["sev_axe"].tolist(), labeled["sev_manual"].tolist()],
            tick_labels=["axe", "manual"],
        )
        plt.title("H1 severity: axe vs manual (normal)")
        plt.ylabel("severity mean (1..5)")
        savefig(figdir, "H1_boxplot_severity.png")

        plot_paired_delta_hist(
            labeled,
            "sev_axe",
            "sev_manual",
            "H1: Δ severity (axe - manual)",
            figdir,
            "H1_hist_delta.png",
        )
    else:
        tests["H1"] = {
            "status": "SKIPPED",
            "reason": "нет достаточной ручной разметки или колонок manual_* / axe_*",
        }

    # ========================================================
    # H2: structure visible-only, normal > low
    # ========================================================
    h2_metrics = [
        ("dom_total_visible", "normal_dom_total_visible", "low_dom_total_visible"),
        ("links_visible", "normal_links_visible", "low_links_visible"),
        ("buttons_visible", "normal_buttons_visible", "low_buttons_visible"),
        ("forms_visible", "normal_forms_visible", "low_forms_visible"),
        ("images_visible", "normal_images_visible", "low_images_visible"),
    ]

    # если builder дал агрегаты, используем и их
    if {"normal_headings_visible", "low_headings_visible"}.issubset(set(pairs_ok.columns)):
        h2_metrics.append(("headings_visible", "normal_headings_visible", "low_headings_visible"))

    if {"normal_landmarks_visible", "low_landmarks_visible"}.issubset(set(pairs_ok.columns)):
        h2_metrics.append(("landmarks_visible", "normal_landmarks_visible", "low_landmarks_visible"))

    tests["H2"] = {}

    for name, a, b in h2_metrics:
        tests["H2"][name] = {
            "wilcoxon": wilcoxon_test(
                pairs_ok[a].astype(float).tolist(),
                pairs_ok[b].astype(float).tolist(),
                alternative="greater",
            ),
            "power_approx": paired_effect_and_power(
                pairs_ok[a].astype(float).tolist(),
                pairs_ok[b].astype(float).tolist(),
                alpha=0.05,
                alternative="greater",
            ),
        }

        plot_paired_delta_hist(
            pairs_ok,
            a,
            b,
            f"H2: Δ {name} (normal - low)",
            figdir,
            f"H2_hist_delta_{name}.png",
        )

    # ========================================================
    # H3: errors
    # ========================================================
    tests["H3"] = {}

    if {"normal_aim_score", "low_aim_score"}.issubset(set(pairs_ok.columns)):
        tests["H3"]["aim_score"] = {
            "wilcoxon": wilcoxon_test(
                pairs_ok["normal_aim_score"].astype(float).tolist(),
                pairs_ok["low_aim_score"].astype(float).tolist(),
                alternative="less",
            ),
            "power_approx": paired_effect_and_power(
                pairs_ok["normal_aim_score"].astype(float).tolist(),
                pairs_ok["low_aim_score"].astype(float).tolist(),
                alpha=0.05,
                alternative="less",
            ),
        }

        plot_paired_delta_hist(
            pairs_ok,
            "normal_aim_score",
            "low_aim_score",
            "H3: Δ aim_score (normal - low)",
            figdir,
            "H3_hist_delta_aim_score.png",
        )

    if {"normal_axe_ratio_all", "low_axe_ratio_all"}.issubset(set(pairs_ok.columns)):
        tests["H3"]["axe_ratio_all"] = {
            "wilcoxon": wilcoxon_test(
                pairs_ok["normal_axe_ratio_all"].astype(float).tolist(),
                pairs_ok["low_axe_ratio_all"].astype(float).tolist(),
                alternative="less",
            ),
            "power_approx": paired_effect_and_power(
                pairs_ok["normal_axe_ratio_all"].astype(float).tolist(),
                pairs_ok["low_axe_ratio_all"].astype(float).tolist(),
                alpha=0.05,
                alternative="less",
            ),
        }

        plot_paired_delta_hist(
            pairs_ok,
            "normal_axe_ratio_all",
            "low_axe_ratio_all",
            "H3: Δ axe_ratio_all (normal - low)",
            figdir,
            "H3_hist_delta_axe_ratio_all.png",
        )

    if {"normal_axe_ratio_noimg_domnoimg", "low_axe_ratio_noimg_domnoimg"}.issubset(set(pairs_ok.columns)):
        tests["H3"]["axe_ratio_noimg_domnoimg"] = {
            "wilcoxon": wilcoxon_test(
                pairs_ok["normal_axe_ratio_noimg_domnoimg"].astype(float).tolist(),
                pairs_ok["low_axe_ratio_noimg_domnoimg"].astype(float).tolist(),
                alternative="greater",
            ),
            "power_approx": paired_effect_and_power(
                pairs_ok["normal_axe_ratio_noimg_domnoimg"].astype(float).tolist(),
                pairs_ok["low_axe_ratio_noimg_domnoimg"].astype(float).tolist(),
                alpha=0.05,
                alternative="greater",
            ),
        }

        plot_paired_delta_hist(
            pairs_ok,
            "normal_axe_ratio_noimg_domnoimg",
            "low_axe_ratio_noimg_domnoimg",
            "H3: Δ axe_ratio_noimg_domnoimg (normal - low)",
            figdir,
            "H3_hist_delta_axe_ratio_noimg.png",
        )

    # ========================================================
    # H4: correlations on normal pages
    # ========================================================
    raw_norm2 = raw[
        (raw["version"] == "normal")
        & (raw["navigation_error"].isna() | (raw["navigation_error"] == ""))
    ].copy()

    tests["H4"] = {
        "dom_total_visible_vs_axe_nodes_count": {
            "spearman": spearman_test(
                raw_norm2["dom_total_visible"].astype(float).tolist(),
                raw_norm2["axe_nodes_count"].astype(float).tolist(),
            ),
            "power_approx": spearman_power_approx(
                raw_norm2["dom_total_visible"].astype(float).tolist(),
                raw_norm2["axe_nodes_count"].astype(float).tolist(),
                alpha=0.05,
                alternative="two-sided",
            ),
        },
        "dom_total_visible_vs_aim_score": {
            "spearman": spearman_test(
                raw_norm2["dom_total_visible"].astype(float).tolist(),
                raw_norm2["aim_score"].astype(float).tolist(),
            ),
            "power_approx": spearman_power_approx(
                raw_norm2["dom_total_visible"].astype(float).tolist(),
                raw_norm2["aim_score"].astype(float).tolist(),
                alpha=0.05,
                alternative="two-sided",
            ),
        },
        "dom_total_vs_axe_nodes_count": {
            "spearman": spearman_test(
                raw_norm2["dom_total"].astype(float).tolist(),
                raw_norm2["axe_nodes_count"].astype(float).tolist(),
            ),
            "power_approx": spearman_power_approx(
                raw_norm2["dom_total"].astype(float).tolist(),
                raw_norm2["axe_nodes_count"].astype(float).tolist(),
                alpha=0.05,
                alternative="two-sided",
            ),
        },
        "dom_total_vs_aim_score": {
            "spearman": spearman_test(
                raw_norm2["dom_total"].astype(float).tolist(),
                raw_norm2["aim_score"].astype(float).tolist(),
            ),
            "power_approx": spearman_power_approx(
                raw_norm2["dom_total"].astype(float).tolist(),
                raw_norm2["aim_score"].astype(float).tolist(),
                alpha=0.05,
                alternative="two-sided",
            ),
        },
        "interpretation": {
            "text": None,
            "delta_rho_abs": None,
        },
    }

    rho_dom = tests["H4"]["dom_total_vs_axe_nodes_count"]["spearman"]["rho"]
    rho_visible = tests["H4"]["dom_total_visible_vs_axe_nodes_count"]["spearman"]["rho"]

    if rho_dom is not None and rho_visible is not None:
        tests["H4"]["interpretation"]["delta_rho_abs"] = abs(rho_dom) - abs(rho_visible)

        if abs(rho_dom) > abs(rho_visible):
            tests["H4"]["interpretation"]["text"] = (
                "Корреляция между dom_total и количеством ошибок выше, чем между "
                "dom_total_visible и количеством ошибок. Это может свидетельствовать "
                "о влиянии скрытых DOM-элементов на результаты автоматического анализа."
            )
        else:
            tests["H4"]["interpretation"]["text"] = (
                "Корреляции для dom_total и dom_total_visible сопоставимы. "
                "Это указывает на то, что основное влияние на количество ошибок "
                "оказывают элементы, фактически отображаемые пользователю."
            )

    plot_scatter(
        raw_norm2,
        "dom_total_visible",
        "axe_nodes_count",
        "H4: dom_total_visible vs axe_nodes_count (normal)",
        figdir,
        "H4_scatter_dom_visible_vs_axe_nodes.png",
    )

    plot_scatter(
        raw_norm2,
        "dom_total_visible",
        "aim_score",
        "H4: dom_total_visible vs aim_score (normal)",
        figdir,
        "H4_scatter_dom_visible_vs_aim_score.png",
    )

    plot_scatter(
        raw_norm2,
        "dom_total",
        "axe_nodes_count",
        "H4: dom_total vs axe_nodes_count (normal)",
        figdir,
        "H4_scatter_dom_total_vs_axe_nodes.png",
    )

    plot_scatter(
        raw_norm2,
        "dom_total",
        "aim_score",
        "H4: dom_total vs aim_score (normal)",
        figdir,
        "H4_scatter_dom_total_vs_aim_score.png",
    )

    dom_aim_points = plot_dom_vs_aim(raw_norm2, figdir)
    dom_aim_points.to_csv(out_dir / "H4_dom_vs_aim_points.csv", index=False)

    # --------------------------------------------------------
    # save json
    # --------------------------------------------------------
    (out_dir / "h_tests.json").write_text(
        json.dumps(tests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # report.md
    # --------------------------------------------------------
    lines: List[str] = []
    lines.append("# Итоговый отчёт H1–H4\n\n")
    lines.append(f"- results_dir: `{results_dir}`\n")
    lines.append(f"- tables_dir: `{in_dir}`\n")
    lines.append(f"- pairs_ok: **{len(pairs_ok)}**\n\n")

    # H1
    lines.append("## H1 (manual vs axe)\n\n")
    if tests["H1"].get("status") == "SKIPPED":
        lines.append(f"Пропущено: {tests['H1'].get('reason')}\n\n")
    else:
        r = tests["H1"]["wilcoxon_manual_gt_axe"]
        pwr = tests["H1"].get("power_approx", {})
        lines.append(f"- размеченных страниц: **{tests['H1']['n_pages_labeled']}**\n\n")
        lines.append("| показатель | значение |\n|---|---:|\n")
        lines.append(f"| n | {r.get('n')} |\n")
        lines.append(f"| statistic | {fmt_num(r.get('statistic'))} |\n")
        lines.append(f"| p_value | {fmt_num(r.get('p_value'))} |\n")
        lines.append(f"| median_delta | {fmt_num(r.get('median_delta'))} |\n")
        lines.append(f"| mean_diff | {fmt_num(pwr.get('mean_diff'))} |\n")
        lines.append(f"| sd_diff | {fmt_num(pwr.get('sd_diff'))} |\n")
        lines.append(f"| dz | {fmt_num(pwr.get('dz'))} |\n")
        lines.append(f"| power_approx | {fmt_num(pwr.get('power_approx'))} |\n")

        lines.append("\n")

    # H2
    lines.append("## H2 (структура, visible-only)\n\n")
    lines.append("| метрика | n | statistic | p_value | median_delta | mean_diff | sd_diff | dz | power_approx | \n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
    for k, block in tests["H2"].items():
        r = block.get("wilcoxon", {})
        pwr = block.get("power_approx", {})
        note = pwr.get("note") or r.get("note") or ""
        lines.append(
            f"| {k} | {r.get('n', 0)} | {fmt_num(r.get('statistic'))} | {fmt_num(r.get('p_value'))} | "
            f"{fmt_num(r.get('median_delta'))} | {fmt_num(pwr.get('mean_diff'))} | {fmt_num(pwr.get('sd_diff'))} | "
            f"{fmt_num(pwr.get('dz'))} | {fmt_num(pwr.get('power_approx'))} | {note} |\n"
        )
    lines.append("\n")

    # H3
    lines.append("## H3 (ошибки: aim + axe)\n\n")
    lines.append("| метрика | n | statistic | p_value | median_delta | mean_diff | sd_diff | dz | power_approx | note |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
    for k, block in tests["H3"].items():
        r = block.get("wilcoxon", {})
        pwr = block.get("power_approx", {})
        note = pwr.get("note") or r.get("note") or ""
        lines.append(
            f"| {k} | {r.get('n', 0)} | {fmt_num(r.get('statistic'))} | {fmt_num(r.get('p_value'))} | "
            f"{fmt_num(r.get('median_delta'))} | {fmt_num(pwr.get('mean_diff'))} | {fmt_num(pwr.get('sd_diff'))} | "
            f"{fmt_num(pwr.get('dz'))} | {fmt_num(pwr.get('power_approx'))} | {note} |\n"
        )
    lines.append("\n")

    # H4
    lines.append("## H4 (корреляции, normal)\n\n")
    lines.append("| метрика | n | rho | p_value | power_approx | note |\n")
    lines.append("|---|---:|---:|---:|---:|---|\n")
    for k, block in tests["H4"].items():
        if k == "interpretation":
            continue
        r = block.get("spearman", {})
        pwr = block.get("power_approx", {})
        note = pwr.get("note") or r.get("note") or ""
        lines.append(
            f"| {k} | {r.get('n', 0)} | {fmt_num(r.get('rho'))} | {fmt_num(r.get('p_value'))} | "
            f"{fmt_num(pwr.get('power_approx'))} | {note} |\n"
        )

    interp = tests["H4"].get("interpretation", {})
    if interp.get("text"):
        lines.append("\n### Интерпретация\n\n")
        lines.append(interp["text"] + "\n\n")

    if interp.get("delta_rho_abs") is not None:
        lines.append(f"- Δ|rho| = {fmt_num(interp.get('delta_rho_abs'))}\n\n")

    lines.append("## Графики\n\n")
    lines.append(f"PNG: `{figdir}`\n")

    (out_dir / "report.md").write_text("".join(lines), encoding="utf-8")

    print("OK")
    print("results:", results_dir)
    print("tables:", in_dir)
    print("report:", out_dir / "report.md")
    print("h_tests:", out_dir / "h_tests.json")
    print("figures:", figdir)


if __name__ == "__main__":
    main()