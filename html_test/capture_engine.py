#=====================================
# This module capture a bandle by URL
#=====================================

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError, # don't mix up with simple Python TimeoutError
    Error as PlaywrightError, # also for clarity
)

from bundle_manager import (
    create_bundle_dir,
    choose_bundle_for_url,
    write_bundle_json,
    write_bundle_text,
    mark_stage_error,
    update_manifest,
    refresh_manifest_validity,
)


# ============================================================
# CONFIG
# ============================================================

AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
DEFAULT_TIMEOUT_MS = 30000
POST_LOAD_WAIT_MS = 3000
HEADLESS = False

TAG_GROUPS = {
    "images": ["img"],
    "links": ["a"],
    "buttons": ["button"],
    "forms": ["form"],
    "h1": ["h1"],
    "h2": ["h2"],
    "h3": ["h3"],
    "h4": ["h4"],
    "h5": ["h5"],
    "h6": ["h6"],
    "ul": ["ul"],
    "ol": ["ol"],
    "header": ["header"],
    "footer": ["footer"],
    "nav": ["nav"],
    "main": ["main"],
    "section": ["section"],
    "article": ["article"],
    "table": ["table"],
}

'''
TAG_TO_GROUP = {}

for group, tags in TAG_GROUPS.items():
    for tag in tags:
        TAG_TO_GROUP[tag] = group
'''
TAG_TO_GROUP = {tag: group for group, tags in TAG_GROUPS.items() for tag in tags}

# ============================================================
# MODELS
# ============================================================


@dataclass
class RuntimeLog:
    console_errors: List[str] = field(default_factory=list)
    console_warnings: List[str] = field(default_factory=list)
    page_errors: List[str] = field(default_factory=list)
    network_errors: List[str] = field(default_factory=list)
    api_errors: List[str] = field(default_factory=list)


@dataclass
class CaptureResult:
    bundle_dir: str
    reused: bool
    url: str
    final_url: Optional[str]
    status: Dict[str, Any]

# ============================================================
# JS HELPERS
# ============================================================


JS_COLLECT_DOM = r'''
() => {
  function getXPath(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return "";
    if (node === document.documentElement) return "/html";
    if (node === document.body) return "/html/body";

    let ix = 0;
    const siblings = node.parentNode ? node.parentNode.childNodes : [];
    for (let i = 0; i < siblings.length; i++) {
      const sibling = siblings[i];
      if (sibling === node) {
        const parentPath = getXPath(node.parentNode);
        return `${parentPath}/${node.tagName.toLowerCase()}[${ix + 1}]`;
      }
      if (sibling.nodeType === Node.ELEMENT_NODE && sibling.tagName === node.tagName) {
        ix++;
      }
    }
    return "";
  }

  function nearestLandmark(el) {
    let current = el;
    while (current) {
      const tag = (current.tagName || "").toLowerCase();
      const role = (current.getAttribute && current.getAttribute("role")) || "";
      if (["nav", "main", "header", "footer", "aside"].includes(tag)) return tag;
      if (role === "navigation") return "nav";
      current = current.parentElement;
    }
    return null;
  }

  function isVisible(el) {
    if (!el) return false;
    if (el.hasAttribute && el.hasAttribute("hidden")) return false;
    const ariaHidden = el.getAttribute && el.getAttribute("aria-hidden");
    if (ariaHidden && ariaHidden.toLowerCase() === "true") return false;

    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === "none") return false;
    if (style.visibility === "hidden" || style.visibility === "collapse") return false;
    if (parseFloat(style.opacity || "1") === 0) return false;

    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    return true;
  }

  const all = Array.from(document.querySelectorAll("*"));
  return all.map((el, index) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const attrs = {};
    for (const attr of Array.from(el.attributes || [])) {
      attrs[attr.name] = attr.value;
    }

    return {
      element_id: `el_${String(index + 1).padStart(6, "0")}`,
      tag: el.tagName.toLowerCase(),
      xpath: getXPath(el),
      selector: el.id ? `#${el.id}` : null,
      text: (el.innerText || el.textContent || "").trim().slice(0, 500),
      attrs,
      role: el.getAttribute("role"),
      visible: isVisible(el),
      children_tags: Array.from(el.children || []).map(x => x.tagName.toLowerCase()).slice(0, 30),
      parent_tag: el.parentElement ? el.parentElement.tagName.toLowerCase() : null,
      has_onclick: !!el.getAttribute("onclick"),
      has_background_image: !!style.backgroundImage && style.backgroundImage !== "none",
      computed_background_image: style.backgroundImage,
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      nearest_landmark: nearestLandmark(el),
      html: el.outerHTML ? el.outerHTML.slice(0, 5000) : null,
    };
  });
}
'''


# ============================================================
# HELPERS
# ============================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def is_api_request(resource_type: str, url: str) -> bool:
    resource_type = (resource_type or "").lower()
    url_l = (url or "").lower()
    if resource_type in {"fetch", "xhr"}:
        return True
    markers = ["/api/", "graphql", "/ajax/", "/rest/", ".json"]
    return any(marker in url_l for marker in markers)

def failure_text(req) -> str:
    failure = req.failure if req.failure else "unknown"
    return str(failure)

def install_runtime_logging(page: Page, runtime: RuntimeLog) -> None:
    # Такой формат описания ф-ии в ф-ии для доступа к данным называется замыкание (closure)
    # Код компактней, не нужно передавать runtime вручную, логически ф-ии сгруппированы и не и
    def on_console(msg) -> None: 
        msg_type = (msg.type or "").lower()
        text = msg.text or ""
        if msg_type == "error":
            runtime.console_errors.append(text)
        elif msg_type in {"warning", "warn"}:
            runtime.console_warnings.append(text)

    def on_page_error(exc) -> None:
        runtime.page_errors.append(str(exc))

    def on_request_failed(req) -> None:
        message = f"{req.method} {req.url} :: {failure_text(req)}"
        runtime.network_errors.append(message)
        try:
            resource_type = req.resource_type
        except Exception:
            resource_type = ""
        if is_api_request(resource_type, req.url):
            runtime.api_errors.append(message)

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    # расшифровка 
    # когда в браузере произойдёт событие requestfailed: 404, 500, CORS, блокировка, timeout
    # вызови функцию on_request_failed
    page.on("requestfailed", on_request_failed)

async def capture_mhtml(page: Page) -> str:
    client = await page.context.new_cdp_session(page)
    snapshot = await client.send("Page.captureSnapshot", {"format": "mhtml"})
    return snapshot.get("data", "") or ""

async def collect_dom(page: Page) -> Dict[str, Any]:
    elements = await page.evaluate(JS_COLLECT_DOM)
    return {
        "status": "success",
        "created_at": utc_now_iso(),
        "dom_total": len(elements),
        "elements": elements,
        "summary": build_dom_summary(elements, visible_only=False),
        "visible_summary": build_dom_summary(elements, visible_only=True),
        "excluded": build_excluded_summary(elements),
        "heading_sequence": [e["tag"] for e in elements if e.get("tag") in {"h1", "h2", "h3", "h4", "h5", "h6"}],
    }

def build_dom_summary(elements: List[Dict[str, Any]], visible_only: bool = False) -> Dict[str, int]:
    summary = {group: 0 for group in TAG_GROUPS.keys()}
    summary["dom_total"] = 0

    for element in elements:
        if visible_only and not element.get("visible"):
            continue
        summary["dom_total"] += 1
        tag = (element.get("tag") or "").lower()
        group = TAG_TO_GROUP.get(tag)
        if group:
            summary[group] += 1

    return summary


# в прошлых версиях excluded было для э-в исключённых из анализа, а в этой версии мы ищём скрытые от пользователя элементы
def build_excluded_summary(elements: List[Dict[str, Any]]) -> Dict[str, int]:
    excluded = {
        "excluded_dom": 0,
        "excluded_links": 0,
        "excluded_buttons": 0,
        "excluded_images": 0,
        "excluded_h1": 0,
        "excluded_h2": 0,
        "excluded_h3": 0,
        "excluded_h4": 0,
        "excluded_ul": 0,
        "excluded_ol": 0,
    }

    for element in elements:
        if element.get("visible"):
            continue
        excluded["excluded_dom"] += 1
        tag = (element.get("tag") or "").lower()
        if tag == "a":
            excluded["excluded_links"] += 1
        elif tag == "button":
            excluded["excluded_buttons"] += 1
        elif tag == "img":
            excluded["excluded_images"] += 1
        elif tag in {"h1", "h2", "h3", "h4"}:
            excluded[f"excluded_{tag}"] += 1
        elif tag == "ul":
            excluded["excluded_ul"] += 1
        elif tag == "ol":
            excluded["excluded_ol"] += 1

    return excluded

async def run_axe(page: Page) -> Dict[str, Any]:
    try:
        await page.add_script_tag(url=AXE_CDN)
        result = await page.evaluate(
            """
            async () => {
              if (!window.axe) {
                return {status: 'error', error_reason: 'axe_not_found', raw: {violations: []}};
              }
              try {
                const raw = await window.axe.run(document, {
                  resultTypes: ['violations'],
                  selectors: true,
                  xpath: true
                });
                return {status: 'success', error_reason: null, raw};
              } catch (e) {
                return {status: 'error', error_reason: String(e), raw: {violations: []}};
              }
            }
            """
        )
    except Exception as exc:
        result = {"status": "error", "error_reason": str(exc), "raw": {"violations": []}}

    raw = result.get("raw") or {"violations": []}
    nodes = normalize_axe_nodes(raw)

    return {
        "status": result.get("status", "error"),
        "created_at": utc_now_iso(),
        "error_reason": result.get("error_reason"),
        "violations_count": len(raw.get("violations", []) or []),
        "nodes_count": len(nodes),
        "impact_summary": impact_summary(nodes),
        "nodes": nodes,
        "raw": raw,
    }

def normalize_axe_nodes(raw_axe: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    idx = 1
    for violation in raw_axe.get("violations", []) or []:
        rule_id = violation.get("id")
        impact = violation.get("impact")
        for node in violation.get("nodes", []) or []:
            targets = node.get("target") or []
            target_xpath = None
            if isinstance(node.get("xpath"), str):
                target_xpath = node.get("xpath")
            elif isinstance(targets, list) and targets:
                target_xpath = str(targets[0])

            nodes.append(
                {
                    "axe_node_uid": f"axe_{idx:06d}",
                    "rule_id": rule_id,
                    "impact": impact,
                    "manual_impact": impact,
                    "manual_overridden": False,
                    "validation_status": "not_checked",
                    "description": violation.get("description"),
                    "help": violation.get("help"),
                    "help_url": violation.get("helpUrl"),
                    "wcag": violation.get("tags", []),
                    "targets": targets,
                    "xpath": target_xpath,
                    "html": node.get("html", ""),
                    "failure_summary": node.get("failureSummary", ""),
                }
            )
            idx += 1
    return nodes

def impact_summary(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0, "unknown": 0}
    for node in nodes:
        impact = node.get("impact") or "unknown"
        if impact not in summary:
            impact = "unknown"
        summary[impact] += 1
    return summary

def make_wave_placeholder(status: str = "pending_manual_review", error_reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "status": status,
        "created_at": utc_now_iso(),
        "metrics": {
            "errors": 0,
            "contrast_errors": 0,
            "alerts": 0,
            "features": 0,
            "structure": 0,
            "aria": 0,
            "aim_score": None,
        },
        "error_reason": error_reason,
        "manual_assignment_required": True,
    }

def make_runtime_json(runtime: RuntimeLog) -> Dict[str, Any]:
    return {
        "status": "success",
        "created_at": utc_now_iso(),
        **asdict(runtime),
        "summary": {
            "console_errors_count": len(runtime.console_errors),
            "console_warnings_count": len(runtime.console_warnings),
            "page_errors_count": len(runtime.page_errors),
            "network_errors_count": len(runtime.network_errors),
            "api_errors_count": len(runtime.api_errors),
        },
    }


# ============================================================
# CAPTURE PIPELINE
# ============================================================

async def capture_url(url: str, mode: str = "normal", bundle_mode: str = "reuse_if_fresh") -> CaptureResult:
    reuse_mode = "reuse_if_actual" if bundle_mode in {"reuse_if_fresh", "reuse_if_actual"} else bundle_mode
    existing_bundle = choose_bundle_for_url(url, mode=reuse_mode)
    if existing_bundle:
        return CaptureResult(
            bundle_dir=str(existing_bundle),
            reused=True,
            url=url,
            final_url=None,
            status={"bundle": "reused"},
        )

    bundle_dir = create_bundle_dir(url, mode=mode)
    runtime = RuntimeLog()
    final_url: Optional[str] = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context: Optional[BrowserContext] = None
        try:
            context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
            page = await context.new_page()
            install_runtime_logging(page, runtime)

            # 1. Open page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                await page.wait_for_timeout(POST_LOAD_WAIT_MS)
                final_url = page.url
                update_manifest(bundle_dir, {"final_url": final_url, "status": {"open": "success"}})
            except PlaywrightTimeoutError as exc:
                mark_stage_error(bundle_dir, "open", f"timeout: {exc}")
                write_bundle_json(bundle_dir, "runtime_json", make_runtime_json(runtime))
                return CaptureResult(str(bundle_dir), False, url, final_url, {"open": "error"})
            except Exception as exc:
                mark_stage_error(bundle_dir, "open", str(exc))
                write_bundle_json(bundle_dir, "runtime_json", make_runtime_json(runtime))
                return CaptureResult(str(bundle_dir), False, url, final_url, {"open": "error"})

            # 2. Original MHTML
            try:
                original_mhtml = await capture_mhtml(page)
                write_bundle_text(bundle_dir, "page_original_mhtml", original_mhtml)
            except Exception as exc:
                mark_stage_error(bundle_dir, "open", f"mhtml_capture_failed: {exc}")

            # 3. DOM
            try:
                dom_json = await collect_dom(page)
                write_bundle_json(bundle_dir, "dom_json", dom_json)
            except Exception as exc:
                mark_stage_error(bundle_dir, "dom", str(exc))

            # 4. AXE
            try:
                axe_json = await run_axe(page)
                write_bundle_json(bundle_dir, "axe_json", axe_json)
            except Exception as exc:
                mark_stage_error(bundle_dir, "axe", str(exc))

            # 5. Runtime
            try:
                runtime_json = make_runtime_json(runtime)
                write_bundle_json(bundle_dir, "runtime_json", runtime_json)
            except Exception as exc:
                mark_stage_error(bundle_dir, "runtime", str(exc))

            # 6. WAVE placeholder for now
            # Later this stage will be replaced by page_wave.mhtml capture after user launches WAVE.
            try:
                wave_json = make_wave_placeholder()
                write_bundle_json(bundle_dir, "wave_json", wave_json)
            except Exception as exc:
                mark_stage_error(bundle_dir, "wave", str(exc))

            refresh_manifest_validity(bundle_dir)
            manifest = update_manifest(bundle_dir, {"final_url": final_url})
            return CaptureResult(str(bundle_dir), False, url, final_url, manifest.get("status", {}))

        finally:
            if context is not None:
                await context.close()
            await browser.close()

# ============================================================
# CLI
# ============================================================


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Capture page into bundle")
    parser.add_argument("url", help="URL to capture")
    parser.add_argument("--mode", default="normal", help="Analysis mode")
    parser.add_argument(
        "--bundle-mode",
        default="reuse_if_fresh",
        choices=["reuse_if_fresh", "reuse_if_actual", "always_recollect"],
        help="Reuse existing fresh bundle or recollect",
    )
    args = parser.parse_args()

    result = await capture_url(args.url, mode=args.mode, bundle_mode=args.bundle_mode)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())