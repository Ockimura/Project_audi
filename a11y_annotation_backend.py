from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import BrowserContext, Page, async_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# Config
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "annotation_runs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
DEFAULT_TIMEOUT_MS = 25000
POST_LOAD_WAIT_MS = 3000
WAVE_EXTENSION_ID = os.getenv("WAVE_EXTENSION_ID", "jbbplnpkjmmeebjpijfedlgcdilocofh")
WAVE_EXTENSION_PATH = os.getenv("WAVE_EXTENSION_PATH", "")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"


# ============================================================
# API models
# ============================================================


class AnalyzePageRequest(BaseModel):
    url: str
    mode: str = "normal"
    wait_ms: int = POST_LOAD_WAIT_MS
    run_axe: bool = True
    open_wave: bool = True
    save_html: bool = True


class AssignWaveRequest(BaseModel):
    run_id: str
    wave_errors: Dict[str, int]


# ============================================================
# Result models
# ============================================================


@dataclass
class StageStatus:
    status: str  # ok | error | skipped | pending
    message: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RawElement:
    element_id: str
    tag: str
    xpath: str
    text: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)
    role: Optional[str] = None
    visible: Optional[bool] = None
    children_tags: List[str] = field(default_factory=list)
    parent_tag: Optional[str] = None
    has_onclick: bool = False
    has_background_image: bool = False
    computed_background_image: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    nearest_landmark: Optional[str] = None
    source_url: Optional[str] = None
    html: Optional[str] = None


@dataclass
class AnnotationRun:
    run_id: str
    url: str
    final_url: Optional[str] = None
    title: Optional[str] = None
    dom_total: int = 0
    status: Dict[str, StageStatus] = field(default_factory=dict)
    raw_elements: List[RawElement] = field(default_factory=list)
    axe_result: Dict[str, Any] = field(default_factory=dict)
    wave_placeholder: Dict[str, Any] = field(default_factory=dict)
    page_html_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    console_messages: List[str] = field(default_factory=list)
    network_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title,
            "dom_total": self.dom_total,
            "status": {k: asdict(v) for k, v in self.status.items()},
            "raw_elements": [asdict(x) for x in self.raw_elements],
            "axe_result": self.axe_result,
            "wave_placeholder": self.wave_placeholder,
            "page_html_path": self.page_html_path,
            "screenshot_path": self.screenshot_path,
            "console_messages": self.console_messages,
            "network_errors": self.network_errors,
        }


RUNS: Dict[str, AnnotationRun] = {}


# ============================================================
# FastAPI
# ============================================================


app = FastAPI(title="A11Y Annotation Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Helpers
# ============================================================


JS_XPATH_HELPER = r'''
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
      if (
        sibling.nodeType === Node.ELEMENT_NODE &&
        sibling.tagName === node.tagName
      ) {
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
      text: (el.innerText || el.textContent || "").trim().slice(0, 500),
      attrs,
      role: el.getAttribute("role"),
      visible: !!(rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none"),
      children_tags: Array.from(el.children || []).map(x => x.tagName.toLowerCase()).slice(0, 20),
      parent_tag: el.parentElement ? el.parentElement.tagName.toLowerCase() : null,
      has_onclick: !!el.getAttribute("onclick"),
      has_background_image: !!style.backgroundImage && style.backgroundImage !== "none",
      computed_background_image: style.backgroundImage,
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      nearest_landmark: nearestLandmark(el),
      source_url: document.location.href,
      html: el.outerHTML ? el.outerHTML.slice(0, 5000) : null,
    };
  });
}
'''


def now_ts() -> float:
    return time.time()



def make_run_id(url: str) -> str:
    digest = hashlib.md5(f"{url}_{time.time()}".encode("utf-8")).hexdigest()[:12]
    return f"run_{digest}"



def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def install_event_logging(page: Page, run: AnnotationRun) -> None:
    page.on("console", lambda msg: run.console_messages.append(f"{msg.type}: {msg.text}"))
    page.on(
        "pageerror",
        lambda exc: run.console_messages.append(f"pageerror: {str(exc)}"),
    )
    page.on(
        "requestfailed",
        lambda req: run.network_errors.append(
            f"{req.method} {req.url} :: {req.failure.error_text if req.failure else 'unknown'}"
        ),
    )


async def new_context(playwright) -> BrowserContext:
    chromium = playwright.chromium
    args = []

    # Optional extension loading for headed mode.
    if WAVE_EXTENSION_PATH:
        args.extend(
            [
                f"--disable-extensions-except={WAVE_EXTENSION_PATH}",
                f"--load-extension={WAVE_EXTENSION_PATH}",
            ]
        )

    browser = await chromium.launch(headless=HEADLESS, args=args)
    return await browser.new_context(viewport={"width": 1440, "height": 1100})


async def navigate(page: Page, url: str, run: AnnotationRun) -> None:
    stage = StageStatus(status="pending", started_at=now_ts())
    run.status["navigation"] = stage
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        await page.wait_for_timeout(POST_LOAD_WAIT_MS)
        run.final_url = page.url
        run.title = await page.title()
        stage.status = "ok"
        stage.message = "Page loaded"
    except PlaywrightTimeoutError as exc:
        stage.status = "error"
        stage.message = f"Navigation timeout: {exc}"
    except Exception as exc:
        stage.status = "error"
        stage.message = f"Navigation failed: {exc}"
    finally:
        stage.finished_at = now_ts()


async def save_page_artifacts(page: Page, run_dir: Path, run: AnnotationRun, save_html: bool = True) -> None:
    stage = StageStatus(status="pending", started_at=now_ts())
    run.status["artifacts"] = stage
    try:
        screenshot_path = run_dir / "page.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        run.screenshot_path = str(screenshot_path)

        if save_html:
            html_path = run_dir / "page.html"
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            run.page_html_path = str(html_path)

        stage.status = "ok"
        stage.message = "Artifacts saved"
    except Exception as exc:
        stage.status = "error"
        stage.message = f"Artifact save failed: {exc}"
    finally:
        stage.finished_at = now_ts()


async def collect_raw_elements(page: Page, run: AnnotationRun) -> None:
    stage = StageStatus(status="pending", started_at=now_ts())
    run.status["dom_collection"] = stage
    try:
        raw_items = await page.evaluate(JS_XPATH_HELPER)
        run.raw_elements = [RawElement(**item) for item in raw_items]
        run.dom_total = len(raw_items)
        stage.status = "ok"
        stage.message = f"Collected {run.dom_total} DOM elements"
    except Exception as exc:
        stage.status = "error"
        stage.message = f"DOM collection failed: {exc}"
    finally:
        stage.finished_at = now_ts()


async def run_axe_scan(page: Page, run: AnnotationRun) -> None:
    stage = StageStatus(status="pending", started_at=now_ts())
    run.status["axe"] = stage
    try:
        await page.add_script_tag(url=AXE_CDN)
        axe_result = await page.evaluate(
            """
            async () => {
              if (!window.axe) {
                return {status: 'error', message: 'axe not found on window'};
              }
              try {
                const result = await window.axe.run(document);
                return {status: 'ok', result};
              } catch (e) {
                return {status: 'error', message: String(e)};
              }
            }
            """
        )
        run.axe_result = axe_result
        if axe_result.get("status") == "ok":
            stage.status = "ok"
            stage.message = f"AXE completed with {len(axe_result['result'].get('violations', []))} violations"
        else:
            stage.status = "error"
            stage.message = axe_result.get("message", "AXE failed")
    except Exception as exc:
        stage.status = "error"
        stage.message = f"AXE execution failed: {exc}"
    finally:
        stage.finished_at = now_ts()


async def open_wave_extension(page: Page, run: AnnotationRun) -> None:
    stage = StageStatus(status="pending", started_at=now_ts())
    run.status["wave"] = stage
    try:
        # Best-effort trigger. Real interaction may still require user action.
        wave_url = f"chrome-extension://{WAVE_EXTENSION_ID}/popup.html"
        run.wave_placeholder = {
            "status": "pending_manual_review",
            "message": "WAVE launch requested. User may need to activate or inspect the extension manually.",
            "extension_url": wave_url,
            "manual_assignment_required": True,
            "errors": {},
        }

        # Try to open extension page in a new tab if extension is loaded.
        context = page.context
        wave_page = await context.new_page()
        try:
            await wave_page.goto(wave_url, timeout=8000)
            stage.details["extension_opened"] = True
            stage.details["extension_url"] = wave_url
            stage.status = "ok"
            stage.message = "WAVE extension page opened. Manual review may still be required."
        except Exception as exc:
            stage.details["extension_opened"] = False
            stage.status = "error"
            stage.message = f"WAVE extension open failed: {exc}"
        finally:
            # Keep page open in headed mode for manual work.
            if HEADLESS:
                await wave_page.close()
    except Exception as exc:
        stage.status = "error"
        stage.message = f"WAVE stage failed: {exc}"
    finally:
        stage.finished_at = now_ts()


async def analyze_page(request: AnalyzePageRequest) -> AnnotationRun:
    run_id = make_run_id(request.url)
    run_dir = OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run = AnnotationRun(run_id=run_id, url=request.url)

    async with async_playwright() as p:
        context = await new_context(p)
        try:
            page = await context.new_page()
            await install_event_logging(page, run)

            await navigate(page, request.url, run)
            if run.status["navigation"].status != "ok":
                RUNS[run_id] = run
                write_json(run_dir / "run.json", run.to_dict())
                return run

            if request.wait_ms > 0:
                await page.wait_for_timeout(request.wait_ms)

            await save_page_artifacts(page, run_dir, run, save_html=request.save_html)
            await collect_raw_elements(page, run)

            if request.run_axe:
                await run_axe_scan(page, run)
            else:
                run.status["axe"] = StageStatus(status="skipped", message="AXE disabled")

            if request.open_wave:
                await open_wave_extension(page, run)
            else:
                run.status["wave"] = StageStatus(status="skipped", message="WAVE disabled")

            RUNS[run_id] = run
            write_json(run_dir / "run.json", run.to_dict())
            return run
        finally:
            await context.close()


# ============================================================
# API endpoints
# ============================================================


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "runs": len(RUNS)}


@app.post("/analyze")
async def analyze_endpoint(payload: AnalyzePageRequest) -> Dict[str, Any]:
    run = await analyze_page(payload)
    return run.to_dict()


@app.get("/runs/{run_id}")
async def get_run(run_id: str) -> Dict[str, Any]:
    run = RUNS.get(run_id)
    if not run:
        path = OUTPUT_DIR / run_id / "run.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@app.post("/runs/{run_id}/wave")
async def assign_wave(run_id: str, payload: AssignWaveRequest) -> Dict[str, Any]:
    if payload.run_id != run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")
    run = RUNS.get(run_id)
    if not run:
        path = OUTPUT_DIR / run_id / "run.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Run not found")
        run_data = json.loads(path.read_text(encoding="utf-8"))
        run = AnnotationRun(
            run_id=run_data["run_id"],
            url=run_data["url"],
            final_url=run_data.get("final_url"),
            title=run_data.get("title"),
            dom_total=run_data.get("dom_total", 0),
            status={k: StageStatus(**v) for k, v in run_data.get("status", {}).items()},
            raw_elements=[RawElement(**x) for x in run_data.get("raw_elements", [])],
            axe_result=run_data.get("axe_result", {}),
            wave_placeholder=run_data.get("wave_placeholder", {}),
            page_html_path=run_data.get("page_html_path"),
            screenshot_path=run_data.get("screenshot_path"),
            console_messages=run_data.get("console_messages", []),
            network_errors=run_data.get("network_errors", []),
        )

    run.wave_placeholder["errors"] = payload.wave_errors
    run.wave_placeholder["status"] = "manually_filled"
    run.wave_placeholder["message"] = "WAVE errors saved manually"
    run.status["wave_manual_fill"] = StageStatus(
        status="ok",
        message="WAVE manual mapping saved",
        started_at=now_ts(),
        finished_at=now_ts(),
        details={"wave_errors": payload.wave_errors},
    )
    RUNS[run_id] = run
    write_json(OUTPUT_DIR / run_id / "run.json", run.to_dict())
    return run.to_dict()


# ============================================================
# Run locally
# ============================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
