# build_dom_table_latest.py
# Автоматически находит последнюю папку results_*/json (по времени в имени results_YYYYMMDD_HHMMSS),
# читает json и делает сводку:
# start_url, normal_file, normal_dom_total, low_vision_file, low_vision_dom_total
#
# ВАЖНО: версии определяются ТОЛЬКО по имени файла:
#   *_normal.json  -> normal
#   *_low_vision.json -> low_vision
#
# Запуск:
#   python build_dom_table_latest.py
#   python build_dom_table_latest.py --root "E:\\Project_audi" --out "dom_table.csv"

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Dict, Tuple


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

    # сортировка по строке YYYYMMDD_HHMMSS лексикографически корректна
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def safe_get(d: Any, path: list[str]) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


@dataclass
class ParsedJson:
    start_url: str
    dom_total: Optional[int]
    file_name: str


def parse_one_json(path: Path) -> Optional[ParsedJson]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None

    start_url = safe_get(data, ["meta", "start_url"])
    dom_total = safe_get(data, ["dom", "dom_total"])

    if isinstance(dom_total, str) and dom_total.isdigit():
        dom_total = int(dom_total)

    if not isinstance(start_url, str) or not start_url.strip():
        return None

    return ParsedJson(
        start_url=start_url.strip(),
        dom_total=dom_total if isinstance(dom_total, int) else None,
        file_name=path.name,
    )


def detect_version_from_filename(filename: str) -> str:
    fn = filename.lower()
    if fn.endswith("_normal.json"):
        return "normal"
    if fn.endswith("_low_vision.json"):
        return "low_vision"
    return ""


def build_rows(json_dir: Path) -> list[dict]:
    # start_url -> {"normal": ParsedJson, "low_vision": ParsedJson}
    grouped: Dict[str, Dict[str, ParsedJson]] = {}

    for p in sorted(json_dir.glob("*.json")):
        v = detect_version_from_filename(p.name)
        if not v:
            continue

        pj = parse_one_json(p)
        if pj is None:
            continue

        grouped.setdefault(pj.start_url, {})
        # если дубликаты одной версии — берём последний встретившийся
        grouped[pj.start_url][v] = pj

    rows: list[dict] = []

    # ВАЖНО: если существует URL только в одной версии, строка всё равно будет,
    # вторая версия останется пустой.
    for start_url in sorted(grouped.keys()):
        normal = grouped[start_url].get("normal")
        low = grouped[start_url].get("low_vision")

        row = {
            "start_url": start_url,
            "normal_file": normal.file_name if normal else "",
            "normal_dom_total": "" if (not normal or normal.dom_total is None) else str(normal.dom_total),
            "low_vision_file": low.file_name if low else "",
            "low_vision_dom_total": "" if (not low or low.dom_total is None) else str(low.dom_total),
        }
        rows.append(row)

    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    fieldnames = [
        "start_url",
        "normal_file",
        "normal_dom_total",
        "low_vision_file",
        "low_vision_dom_total",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        type=str,
        default=".",
        help="Корневая папка, где лежат results_*/ (например E:\\Project_audi)",
    )
    ap.add_argument(
        "--out",
        type=str,
        default="dom_table.csv",
        help="Куда сохранить CSV (по умолчанию рядом со скриптом)",
    )
    args = ap.parse_args()

    root = Path(args.root)
    json_dir = find_latest_results_json_dir(root)

    rows = build_rows(json_dir)

    out_path = Path(args.out)
    write_csv(rows, out_path)

    print(f"JSON_DIR: {json_dir}")
    print(f"OK: {len(rows)} строк(и) -> {out_path}")


if __name__ == "__main__":
    main()