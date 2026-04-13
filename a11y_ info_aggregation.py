#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fill_audi.py

Сборщик: Playwright + axe-core + WAVE (через captureSnapshot MHTML).

Что делает:
- DOM-структура (с учётом visible) + элементы (xpath, visible).
- axe violations/nodes + impact_summary.
- WAVE метрики из MHTML (Wave_Error, Wave_Contrast_Error, Wave_Alerts, Wave_Features, Wave_Structure, Wave_Aria, aim_score).
- meta.aim_score дублируется из wave.metrics.aim_score.
- manual секция создаётся сразу:
    - manual.impact_summary = копия по manual_impact (изначально impact)
    - manual.nodes_count
    - в каждой ноде: manual_impact, manual_overridden

Важно:
- Этот файл ориентирован на интерактивный процесс (pause/resume), как у тебя было.
"""

from __future__ import annotations

import json
import hashlib
import asyncio
import shutil
import re
from typing import cast, Any, Dict, List, Optional
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from datetime import datetime, timedelta, timezone

from tqdm.auto import tqdm
from lxml import html
from playwright.async_api import async_playwright, TimeoutError, Error as PlaywrightError


# =====================================================
# CONFIG
# =====================================================
URLS_FILE = "urls_have_low.txt"

# Рекомендуется писать в results_YYYYMMDD_HHMMSS, чтобы анализатор мог брать latest
RUN_DIR = Path("results_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
JSON_DIR = RUN_DIR / "json"
MHTML_DIR = RUN_DIR / "mhtml"

MODE = "low_vision"
WAVE_EXTENSION_PATH = Path("./wave_extension").resolve()
WAVE_ID = "jbbplnpkjmmeebjpijfedlgcdilocofh"

TAG_GROUPS = {
    "images": ["img"],
    "links": ["a"],
    "buttons": ["button"],
    "forms": ["form"],
    "h1": ["h1"], "h2": ["h2"], "h3": ["h3"], "h4": ["h4"],
    "ul": ["ul"], "ol": ["ol"],
    "header": ["header"], "footer": ["footer"],
    "nav": ["nav"], "main": ["main"],
    "section": ["section"], "article": ["article"],
}
tag_map = {t: g for g, tags in TAG_GROUPS.items() for t in tags}

max_retries = 3

RUN_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)
MHTML_DIR.mkdir(parents=True, exist_ok=True)

SEVERITIES = ("critical", "serious", "moderate", "minor")


# =====================================================
# UTILS
# =====================================================
def md5_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:8]

def sha1_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def element_uid(xpath: str) -> str:
    return "el_" + sha1_hash(xpath)[:6]

def axe_node_uid(rule_id: Any, impact: Any, html_snippet: Any, targets: Any) -> str:
    flat_targets = [str(t) for t in (targets or [])]
    base = (
        str(rule_id or "") + "|" +
        str(impact or "") + "|" +
        str(html_snippet or "").replace("\n", " ").strip() + "|" +
        "|".join(sorted(flat_targets))
    )
    return "axe_" + sha1_hash(base)[:12]

def ensure_manual_section(result: Dict[str, Any]) -> None:
    """
    Инициализация manual-структуры на базе axe (без фейков).
    """
    axe = result.get("axe")
    if not isinstance(axe, dict):
        result["manual"] = {"impact_summary": {k: 0 for k in SEVERITIES}, "nodes_count": 0, "notes": "", "updated_at": None}
        return

    nodes = axe.get("nodes")
    if not isinstance(nodes, list):
        nodes = []

    # Инициализация полей в нодах
    for n in nodes:
        if not isinstance(n, dict):
            continue
        impact = n.get("impact")
        if n.get("manual_impact") is None:
            n["manual_impact"] = impact
        if "manual_overridden" not in n:
            n["manual_overridden"] = False

    # Сводка
    summary = {k: 0 for k in SEVERITIES}
    cnt = 0
    for n in nodes:
        if not isinstance(n, dict):
            continue
        mi = n.get("manual_impact")
        if isinstance(mi, str):
            mi2 = mi.strip().lower()
            if mi2 in summary:
                summary[mi2] += 1
                cnt += 1

    result["manual"] = {
        "impact_summary": summary,
        "nodes_count": cnt,
        "notes": "",
        "updated_at": None,
    }


# ---------------- Visibility (точнее) ----------------
async def compute_visibility(page, xpaths: List[str]) -> Dict[str, bool]:
    script = """
    (xpaths) => {
      const results = {};
      const isHiddenByAttr = (el) => {
        if (!el) return true;
        if (el.hasAttribute && el.hasAttribute('hidden')) return true;
        const ah = el.getAttribute && el.getAttribute('aria-hidden');
        if (ah && ah.toLowerCase() === 'true') return true;
        return false;
      };

      const isVisible = (el) => {
        if (!el) return false;
        if (isHiddenByAttr(el)) return false;

        const style = window.getComputedStyle(el);
        if (!style) return false;

        if (style.display === 'none') return false;
        if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
        if (parseFloat(style.opacity || '1') === 0) return false;

        const rects = el.getClientRects();
        if (!rects || rects.length === 0) return false;

        const r = el.getBoundingClientRect();
        if (!r || r.width <= 0 || r.height <= 0) return false;

        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;

        const inViewport = cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight;
        if (inViewport) {
          const topEl = document.elementFromPoint(cx, cy);
          if (topEl && (topEl === el || el.contains(topEl))) return true;
        }

        return true;
      };

      for (const xp of xpaths) {
        try {
          const el = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
          results[xp] = isVisible(el);
        } catch (e) {
          results[xp] = false;
        }
      }
      return results;
    }
    """
    return await page.evaluate(script, xpaths)

async def get_visibility_safe(page, all_elements, tree) -> Dict[str, bool]:
    xpaths = [tree.getpath(el) for el in all_elements]
    try:
        return await compute_visibility(page, xpaths)
    except Exception:
        return {xp: False for xp in xpaths}


# ---------------- Navigation & DOM ----------------
async def page_goto(page, url: str, retries: int = max_retries):
    for i in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return True, None
        except (TimeoutError, PlaywrightError) as e:
            if i == retries - 1:
                return False, str(e)
            print(f"  [Retry {i+1}/{retries}] Ошибка: {url}. Пробуем снова...")
            await asyncio.sleep(2)
    return False, "Неизвестная ошибка при навигации"

async def get_stable_page_content(page, retries: int = 5, delay: float = 0.6) -> str:
    last_error = None
    for _ in range(retries):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
            await page.wait_for_load_state("networkidle", timeout=8000)
            await asyncio.sleep(delay)
            return await page.content()
        except PlaywrightError as e:
            last_error = e
            await asyncio.sleep(delay)
    raise PlaywrightError(f"Не удалось получить стабильный DOM после {retries} попыток: {last_error}")


# ---------------- WAVE parsing (из MHTML) ----------------
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
        # иногда WAVE пишет aim-score-value (как в backfill)
        'id="aim-score-value"', "id='aim-score-value'",
    ):
        if k in html_text:
            score += 3

    if "wave" in html_text.lower():
        score += 1
    if "webaim" in html_text.lower():
        score += 1

    return score

def extract_wave_from_html_text_strict(html_text: str) -> Dict[str, Optional[int]]:
    """
    Достаём метрики WAVE из html-кандидата.
    aim_score у WAVE бывает:
    - id="aim_score" ... > 7 <
    - или id="aim-score-value" ... > 7.3 </span> out of 10
    """
    metrics: Dict[str, Optional[int]] = {
        "Wave_Error": 0,
        "Wave_Contrast_Error": 0,
        "Wave_Alerts": 0,
        "Wave_Features": 0,
        "Wave_Structure": 0,
        "Wave_Aria": 0,
        "aim_score": None,
    }

    mapping_int = {
        "Wave_Error": r'id\s*=\s*["\']error["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "Wave_Contrast_Error": r'id\s*=\s*["\']contrastnum["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "Wave_Alerts": r'id\s*=\s*["\']alert["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "Wave_Features": r'id\s*=\s*["\']feature["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "Wave_Structure": r'id\s*=\s*["\']structure["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "Wave_Aria": r'id\s*=\s*["\']aria["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
        "aim_score": r'id\s*=\s*["\']aim_score["\'][\s\S]{0,200}?>\s*(\d+)\s*<',
    }

    for key, pattern in mapping_int.items():
        m = re.search(pattern, html_text, flags=re.IGNORECASE)
        if m:
            try:
                metrics[key] = int(m.group(1))
            except Exception:
                pass

    # fallback для aim-score-value (float out of 10)
    if metrics.get("aim_score") is None:
        m2 = re.search(
            r'id=3D["\']aim-score-value["\']\s*>\s*([0-9]+(?:\.[0-9]+)?)\s*</span>\s*out\s+of\s+10',
            html_text,
            flags=re.IGNORECASE,
        )
        if m2:
            try:
                # округлим до int, потому что твой текущий формат aim_score в JSON везде как число (можно и float, если хочешь)
                metrics["aim_score"] = int(float(m2.group(1)))
            except Exception:
                pass

    return metrics

def mhtml_to_html_candidates(mhtml_raw: str) -> List[str]:
    raw_bytes = mhtml_raw.encode("utf-8", errors="ignore")
    msg: Message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    html_parts: List[str] = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = (part.get_content_type() or "").lower()
        if ctype not in ("text/html", "application/xhtml+xml"):
            continue

        payload_any = part.get_payload(decode=True)
        if payload_any is None:
            continue
        payload = cast(bytes, payload_any)

        charset = part.get_content_charset() or "utf-8"
        try:
            html_parts.append(payload.decode(charset, errors="ignore"))
        except LookupError:
            html_parts.append(payload.decode("utf-8", errors="ignore"))

    return html_parts


# =====================================================
# PAGE ANALYSIS
# =====================================================
async def analyze_page(page, url: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "meta": {
            "start_url": url,
            "final_url": None,
            "version": MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "wave_status": None,
            "aim_score": None,
        },
        "navigation_error": None,
        "page_crashed": None,
        "spa_suspected": None,
        "dom": {},
        "excluded": {},
        "elements": [],
        "axe": {"violations_count": 0, "nodes_count": 0, "impact_summary": {}, "nodes": []},
        "manual": None,  # будет заполнено после axe
        "cdp": {"snapshot_saved": False, "error": None},
        "wave": {},
    }

    # 1) Навигация
    success, nav_error = await page_goto(page, url)
    if not success:
        result["navigation_error"] = nav_error
        return result

    result["meta"]["final_url"] = page.url

    # 2) Пауза: подготовка страницы (включая переключение low vision, если нужно)
    await page.pause()
    try:
        user_input = input("Команда (Enter/error): ").strip().lower()
    except EOFError:
        user_input = "error"

    if page.is_closed():
        result["navigation_error"] = "inspector_closed_manually"
        return result

    if user_input == "error":
        result["navigation_error"] = "forced_skip_by_user"
        return result

    try:
        # ---------------- DOM ----------------
        html_source = await get_stable_page_content(page)
        root = html.fromstring(html_source)
        tree = root.getroottree()

        all_elements = root.xpath("//*")
        xpaths = [tree.getpath(el) for el in all_elements]
        visibility = await get_visibility_safe(page, all_elements, tree)

        elements = []
        dom_counts = {f"{g}{v}": 0 for g in TAG_GROUPS for v in ["", "_visible"]}

        for el, xp in zip(all_elements, xpaths):
            tag = (el.tag or "").lower()
            visible = bool(visibility.get(xp, False))
            group = tag_map.get(tag)

            if group:
                dom_counts[group] += 1
                if visible:
                    dom_counts[f"{group}_visible"] += 1

            elements.append({
                "element_id": element_uid(xp),
                "tag": tag,
                "xpath": xp,
                "visible": visible,
            })

        result["dom"] = {**dom_counts, "dom_total": len(all_elements)}
        result["elements"] = elements

        # ---------------- AXE ----------------
        try:
            await page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js")
            axe_results = await page.evaluate("axe.run({ resultTypes: ['violations'], selectors: true, xpath: true })")
        except Exception:
            axe_results = {"violations": []}

        impact_summary = {k: 0 for k in SEVERITIES}
        axe_nodes: List[Dict[str, Any]] = []

        for v in axe_results.get("violations", []) or []:
            rule_id = v.get("id")
            impact = v.get("impact")
            tags = v.get("tags", [])
            desc = v.get("description")
            help_text = v.get("help")
            help_url = v.get("helpUrl")

            for n in v.get("nodes", []) or []:
                if isinstance(impact, str) and impact in impact_summary:
                    impact_summary[impact] += 1

                uid = axe_node_uid(rule_id, impact, n.get("html", ""), n.get("target", []))

                node_xpath = n.get("xpath")  # если axe вернул
                if not isinstance(node_xpath, str) or not node_xpath.strip():
                    node_xpath = None

                axe_nodes.append({
                    "axe_node_uid": uid,
                    "rule_id": rule_id,
                    "impact": impact,
                    "wcag": tags,
                    "description": desc,
                    "help": help_text,
                    "help_url": help_url,
                    "targets": n.get("target", []),
                    "xpath": node_xpath,
                    "html": n.get("html", ""),
                    "manual_impact": None,          # заполнится ensure_manual_section
                    "manual_overridden": False,    # заполнится ensure_manual_section
                })

        result["axe"] = {
            "violations_count": len(axe_results.get("violations", []) or []),
            "nodes_count": len(axe_nodes),
            "impact_summary": impact_summary,
            "nodes": axe_nodes,
        }

        # Создаём manual сразу (копия из axe)
        ensure_manual_section(result)

        # ---------------- WAVE ----------------
        tqdm.write(f"--- Запустите WAVE на {page.url} и нажмите Resume в Inspector ---")
        await page.pause()

        try:
            user_input = input("Команда (Enter/error): ").strip().lower()
        except EOFError:
            user_input = "error"

        if page.is_closed():
            result["navigation_error"] = "inspector_closed_manually"
            return result

        if user_input == "error":
            result["navigation_error"] = "forced_skip_by_user"
            return result

        try:
            client = await page.context.new_cdp_session(page)
            snapshot = await client.send("Page.captureSnapshot", {"format": "mhtml"})
            result["cdp"]["snapshot_saved"] = True

            mhtml_raw_data = snapshot.get("data", "") or ""
            mhtml_path = MHTML_DIR / f"{md5_hash(url)}_wave_{MODE}.mhtml"
            mhtml_path.write_text(mhtml_raw_data, encoding="utf-8", errors="ignore", newline="\n")

            html_candidates = mhtml_to_html_candidates(mhtml_raw_data)

            best = None
            best_score = -1
            for cand in html_candidates:
                s = score_wave_candidate(cand)
                if s > best_score:
                    best_score = s
                    best = cand

            wave_metrics = extract_wave_from_html_text_strict(best or "")

            # fallback: ищем часть, где реально есть числа
            check_values = [v for k, v in wave_metrics.items() if k != "aim_score"]
            if best_score <= 0 or all(int(v or 0) == 0 for v in check_values):
                for cand in html_candidates:
                    wm = extract_wave_from_html_text_strict(cand)
                    cv = [v for k, v in wm.items() if k != "aim_score"]
                    if not all(int(v or 0) == 0 for v in cv):
                        wave_metrics = wm
                        break

            check_values = [v for k, v in wave_metrics.items() if k != "aim_score"]
            if all(int(v or 0) == 0 for v in check_values):
                result["wave"] = {
                    "status": "error",
                    "error_reason": "no_wave_markers_found_in_mhtml",
                    "metrics": wave_metrics,
                }
                result["meta"]["wave_status"] = "not_detected"
            else:
                result["wave"] = {"status": "ok", "error_reason": None, "metrics": wave_metrics}
                result["meta"]["wave_status"] = "ok"

            # Дублирование aim_score в meta
            result["meta"]["aim_score"] = wave_metrics.get("aim_score")

        except Exception as e:
            result["cdp"]["error"] = str(e)
            result["wave"] = {"status": "error", "error_reason": "cdp_snapshot_failed", "metrics": {}}
            result["meta"]["wave_status"] = "cdp_error"

    except Exception as e:
        result["navigation_error"] = f"lost_after_pause: {e}"
        return result

    return result


# =====================================================
# RUNNER
# =====================================================
async def main():
    urls = [l.strip() for l in Path(URLS_FILE).read_text(encoding="utf-8").splitlines() if l.strip()]

    run_profile_dir = Path("./profile_run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    if run_profile_dir.exists():
        shutil.rmtree(run_profile_dir, ignore_errors=True)

    context = None
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(run_profile_dir),
                headless=False,
                ignore_https_errors=True,
                args=[
                    f"--disable-extensions-except={WAVE_EXTENSION_PATH}",
                    f"--load-extension={WAVE_EXTENSION_PATH}",
                ],
            )

            check = await context.new_page()
            await check.goto("chrome://extensions/", wait_until="load")
            input("\n--- Убедитесь, что WAVE включён, Enter ---\n")
            await check.close()

            page = await context.new_page()

            for i, url in enumerate(tqdm(urls, desc="Анализ сайтов"), 1):
                tqdm.write(f"[{i}/{len(urls)}] {url}")
                data = await analyze_page(page, url)

                out = JSON_DIR / f"{md5_hash(url)}_{MODE}.json"
                out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tqdm.write(f"✅ {out.name}")

        finally:
            if context is not None:
                await context.close()
            if run_profile_dir.exists():
                shutil.rmtree(run_profile_dir, ignore_errors=True)
                tqdm.write(f"🧹 Временный профиль {run_profile_dir.name} удален.")


if __name__ == "__main__":
    asyncio.run(main())