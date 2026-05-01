from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, List
from urllib.parse import urlparse


# ============================================================
# CONFIG
# ============================================================

RESULTS_DIR = Path("results") # project/results
BUNDLES_DIR = RESULTS_DIR / "bundles" # project/results/bundles
BUNDLES_DIR.mkdir(parents=True, exist_ok=True) #create dir project/results if project exsist and if dict exsist it isn't error

BUNDLE_TIME_FORMAT = "%d_%m_%y__%H_%M_%S"
MANIFEST_NAME = "bundle_manifest.json"

# Обязательные файлы bundle.
REQUIRED_FILES = { 
    "page_original_mhtml": "page_original.mhtml",
    "dom_json": "dom.json",
    "axe_json": "axe.json",
    "wave_json": "wave.json",
}

# Дополнительные файлы.
OPTIONAL_FILES = { 
    "page_wave_mhtml": "page_wave.mhtml",
    "runtime_json": "runtime.json",
    "components_json": "components.json",
    "component_dictionary_local_json": "component_dictionary_local.json",
}

# DOM / AXE / WAVE должны быть собраны с разницей не больше 5 минут.
MAX_ANALYSIS_SPREAD = timedelta(minutes=5)
# MHTML должен быть связан с анализом по времени: не дальше 3 часов.
MAX_MHTML_TO_ANALYSIS_DELTA = timedelta(hours=3)
MAX_ACTUAL_AGE = timedelta(days=1)


# ============================================================
# MODELS
# ============================================================


@dataclass # Описывает структуру manifest.
class BundleManifest:
    bundle_id: str
    url: str
    final_url: Optional[str]
    mode: str
    created_at: str
    safe_url: str
    bundle_dir: str
    # Список файлов, времена сохранения этапов и статусы.
    files: Dict[str, str]
    timestamps: Dict[str, Optional[str]]
    status: Dict[str, Any]


# ============================================================
# TIME HELPERS
# ============================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)



def iso_now() -> str:
    return utc_now().isoformat()



def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ============================================================
# SAFE URL / BUNDLE NAME
# ============================================================


def make_safe_url(url: str, max_length: int = 140) -> str:
    """
    Converts URL to a filesystem-safe name.

    Example:
    https://sledcom.ru/news?id=1 -> sledcom.ru_news_id_1
    """
    parsed = urlparse(url.strip())

    if parsed.netloc:
        raw = parsed.netloc + parsed.path
        if parsed.query:
            raw += "_" + parsed.query
    else:
        raw = url.strip()
        raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)

    raw = raw.strip().strip("/")
    safe = re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "_", raw)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("_")

    if not safe:
        safe = "unknown_url"

    return safe[:max_length]



def make_bundle_id(url: str, created_at: Optional[datetime] = None) -> str:
    dt = created_at or utc_now()
    safe_url = make_safe_url(url)
    return f"{safe_url}__{dt.strftime(BUNDLE_TIME_FORMAT)}"


# ============================================================
# BUNDLE CREATION
# ============================================================


def create_bundle_dir(url: str, mode: str = "normal", final_url: Optional[str] = None) -> Path:
    created = utc_now()
    bundle_id = make_bundle_id(url, created)
    bundle_dir = BUNDLES_DIR / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=False)

    files = {**REQUIRED_FILES, **OPTIONAL_FILES}
    manifest = BundleManifest(
        bundle_id=bundle_id,
        url=url,
        final_url=final_url,
        mode=mode,
        created_at=created.isoformat(),
        safe_url=make_safe_url(url),
        bundle_dir=str(bundle_dir),
        files=files,
        timestamps={
            "page_original_mhtml": None,
            "page_wave_mhtml": None,
            "dom": None,
            "axe": None,
            "wave": None,
            "runtime": None,
            "components": None,
        },
        status={
            "open": "pending",
            "dom": "pending",
            "axe": "pending",
            "wave": "pending",
            "runtime": "pending",
            "components": "not_started",
            "valid": False,
            "actual": False,
        },
    )
    save_manifest(bundle_dir, manifest)
    return bundle_dir


# ============================================================
# MANIFEST IO
# ============================================================


def manifest_path(bundle_dir: Path) -> Path:
    return bundle_dir / MANIFEST_NAME



def save_manifest(bundle_dir: Path, manifest: BundleManifest | Dict[str, Any]) -> None:
    payload = asdict(manifest) if isinstance(manifest, BundleManifest) else manifest
    manifest_path(bundle_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )



def load_manifest(bundle_dir: Path) -> Dict[str, Any]:
    path = manifest_path(bundle_dir)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))



def update_manifest(bundle_dir: Path, updates: Dict[str, Any]) -> Dict[str, Any]:
    manifest = load_manifest(bundle_dir)

    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(manifest.get(key), dict):
            manifest[key].update(value)
        else:
            manifest[key] = value

    save_manifest(bundle_dir, manifest)
    return manifest



def mark_file_saved(
    bundle_dir: Path,
    logical_name: str,
    status_key: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Updates manifest timestamp/status after saving a file.

    Example:
    mark_file_saved(bundle_dir, "dom", "dom")
    """
    ts = timestamp or iso_now()
    manifest = load_manifest(bundle_dir)

    manifest.setdefault("timestamps", {})[logical_name] = ts

    if status_key:
        manifest.setdefault("status", {})[status_key] = "success"

    save_manifest(bundle_dir, manifest)
    return manifest



def mark_stage_error(bundle_dir: Path, status_key: str, error: str) -> Dict[str, Any]:
    manifest = load_manifest(bundle_dir)
    manifest.setdefault("status", {})[status_key] = "error"
    manifest.setdefault("errors", {})[status_key] = error
    save_manifest(bundle_dir, manifest)
    return manifest


# ============================================================
# BUNDLE VALIDATION
# ============================================================


def required_files_exist(bundle_dir: Path) -> bool:
    for filename in REQUIRED_FILES.values():
        if not (bundle_dir / filename).exists():
            return False
    return True



def get_analysis_timestamps(manifest: Dict[str, Any]) -> List[datetime]:
    timestamps = manifest.get("timestamps", {})
    values = [
        parse_iso_datetime(timestamps.get("dom")),
        parse_iso_datetime(timestamps.get("axe")),
        parse_iso_datetime(timestamps.get("wave")),
    ]
    return [v for v in values if v is not None]



def get_mhtml_timestamp(manifest: Dict[str, Any]) -> Optional[datetime]:
    timestamps = manifest.get("timestamps", {})
    return parse_iso_datetime(timestamps.get("page_original_mhtml"))



def analysis_spread_ok(manifest: Dict[str, Any]) -> bool:
    values = get_analysis_timestamps(manifest)
    if len(values) < 3:
        return False
    return max(values) - min(values) <= MAX_ANALYSIS_SPREAD



def mhtml_relation_ok(manifest: Dict[str, Any]) -> bool:
    values = get_analysis_timestamps(manifest)
    mhtml_ts = get_mhtml_timestamp(manifest)
    if len(values) < 3 or not mhtml_ts:
        return False
    newest_analysis = max(values)
    return abs(newest_analysis - mhtml_ts) <= MAX_MHTML_TO_ANALYSIS_DELTA



def is_bundle_valid(bundle_dir: Path) -> bool:
    try:
        manifest = load_manifest(bundle_dir)
    except Exception:
        return False

    return (
        required_files_exist(bundle_dir)
        and analysis_spread_ok(manifest)
        and mhtml_relation_ok(manifest)
    )



def is_bundle_actual(bundle_dir: Path, now: Optional[datetime] = None) -> bool:
    try:
        manifest = load_manifest(bundle_dir)
    except Exception:
        return False

    values = get_analysis_timestamps(manifest)
    if len(values) < 3:
        return False

    current = now or utc_now()
    newest_analysis = max(values)

    return (
        current - newest_analysis <= MAX_ACTUAL_AGE
        and analysis_spread_ok(manifest)
    )



def refresh_manifest_validity(bundle_dir: Path) -> Dict[str, Any]:
    valid = is_bundle_valid(bundle_dir)
    actual = is_bundle_actual(bundle_dir)
    return update_manifest(
        bundle_dir,
        {
            "status": {
                "valid": valid,
                "actual": actual,
            }
        },
    )


# ============================================================
# BUNDLE SEARCH
# ============================================================


def list_bundles() -> List[Path]:
    if not BUNDLES_DIR.exists():
        return []
    return [p for p in BUNDLES_DIR.iterdir() if p.is_dir()]



def find_bundles_for_url(url: str) -> List[Path]:
    safe = make_safe_url(url)
    candidates = []
    for bundle_dir in list_bundles():
        if bundle_dir.name.startswith(safe + "__"):
            candidates.append(bundle_dir)
    return sorted(candidates, key=lambda p: p.name, reverse=True)



def find_latest_bundle(url: str) -> Optional[Path]:
    bundles = find_bundles_for_url(url)
    return bundles[0] if bundles else None



def find_latest_valid_bundle(url: str) -> Optional[Path]:
    for bundle_dir in find_bundles_for_url(url):
        if is_bundle_valid(bundle_dir):
            return bundle_dir
    return None



def find_latest_actual_bundle(url: str) -> Optional[Path]:
    for bundle_dir in find_bundles_for_url(url):
        if is_bundle_valid(bundle_dir) and is_bundle_actual(bundle_dir):
            return bundle_dir
    return None


# ============================================================
# REUSE / RECOLLECT DECISION
# ============================================================


def choose_bundle_for_url(url: str, mode: str = "reuse_if_actual") -> Optional[Path]:
    """
    mode:
    - reuse_if_actual: return actual valid bundle if available, otherwise None
    - always_recollect: always return None
    """
    if mode == "always_recollect":
        return None
    if mode == "reuse_if_actual":
        return find_latest_actual_bundle(url)
    raise ValueError(f"Unknown mode: {mode}")


# ============================================================
# FILE PATH HELPERS
# ============================================================


def bundle_file(bundle_dir: Path, logical_name: str) -> Path:
    manifest = load_manifest(bundle_dir)
    files = manifest.get("files", {})
    filename = files.get(logical_name)
    if not filename:
        raise KeyError(f"Unknown logical file name: {logical_name}")
    return bundle_dir / filename



def write_bundle_json(bundle_dir: Path, logical_name: str, payload: Dict[str, Any]) -> Path:
    path = bundle_file(bundle_dir, logical_name)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    status_key_map = {
        "dom_json": "dom",
        "axe_json": "axe",
        "wave_json": "wave",
        "runtime_json": "runtime",
        "components_json": "components",
    }
    timestamp_key_map = {
        "dom_json": "dom",
        "axe_json": "axe",
        "wave_json": "wave",
        "runtime_json": "runtime",
        "components_json": "components",
    }

    mark_file_saved(
        bundle_dir,
        timestamp_key_map.get(logical_name, logical_name),
        status_key_map.get(logical_name),
    )
    refresh_manifest_validity(bundle_dir)
    return path



def write_bundle_text(bundle_dir: Path, logical_name: str, content: str) -> Path:
    path = bundle_file(bundle_dir, logical_name)
    path.write_text(content, encoding="utf-8", errors="ignore")

    status_key_map = {
        "page_original_mhtml": "open",
        "page_wave_mhtml": "wave",
    }
    timestamp_key_map = {
        "page_original_mhtml": "page_original_mhtml",
        "page_wave_mhtml": "page_wave_mhtml",
    }

    mark_file_saved(
        bundle_dir,
        timestamp_key_map.get(logical_name, logical_name),
        status_key_map.get(logical_name),
    )
    refresh_manifest_validity(bundle_dir)
    return path


# ============================================================
# CLI TEST
# ============================================================


if __name__ == "__main__":
    test_url = "https://sledcom.ru/"
    bundle = choose_bundle_for_url(test_url, mode="reuse_if_actual")

    if bundle:
        print(f"Reuse actual bundle: {bundle}")
    else:
        bundle = create_bundle_dir(test_url, mode="normal")
        print(f"Created bundle: {bundle}")
        print(f"Manifest: {manifest_path(bundle)}")
