'''
Как снять axe.json из DevTools
Откройте DevTools -> Console и вставьте
(async () => {
  const script = document.createElement('script');
  script.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.8.2/axe.min.js';
  document.head.appendChild(script);

  await new Promise(resolve => script.onload = resolve);

  const results = await axe.run();

  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(results, null, 2));

  const downloadAnchorNode = document.createElement('a');
  downloadAnchorNode.setAttribute("href", dataStr);
  downloadAnchorNode.setAttribute("download", "axe.json");
  document.body.appendChild(downloadAnchorNode);
  downloadAnchorNode.click();
  downloadAnchorNode.remove();
})();

Процесс работы:
1. Запустить скрипт
2. Ввести URL -> получить {name} для файлов
3. Открыть сайт
4. В консоли вставить скрипт выше
5. Сохранить с предложенным именем {name}_axe.json
6. Расположить файл в той же папке, что и скрипт, он сам подхватит содержимое
'''

import json
import hashlib
import re
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, List
from pathlib import Path
from urllib.parse import urlparse

RESULTS_DIR = Path("url") # html_test/url
TIME_FORMAT = "%d_%m_%y__%H_%M_%S"

BASE_TYPES = [
    "button",
    "link",
    "nav",
    "form",
    "input",
    "image",
    "card",
    "modal",
    "dropdown",
    "list",
    "header",
    "footer",
    "section"
]

# ============================================================
# TIME HELPERS
# ============================================================

# Возвращает текущее время UTC.
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Возвращает текущее время строкой.
def iso_now() -> str:
    return utc_now().isoformat()


# Преобразует строку времени обратно в datetime. Если времени нет или оно сломано — вернёт None.
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
    parsed = urlparse(url.strip()) # Разбирает URL.

    if parsed.netloc: # Берёт домен + путь.
        raw = parsed.netloc + parsed.path
        if parsed.query: # Добавляет query-параметры.
            raw += "_" + parsed.query
    else:
        raw = url.strip()
        # если остался https чистим вручную
        raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE)

    raw = raw.strip().strip("/")
    # Все опасные символы заменяет на _
    safe = re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "_", raw)
    # Несколько _ подряд заменяет на один.
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("_")

    if not safe:
        safe = "unknown_url"
    # Обрезает слишком длинное имя.
    return safe[:max_length]

# Создаёт полное имя manual.
def make_results_id(url: str, created_at: Optional[datetime] = None) -> str:
    dt = created_at or utc_now()
    safe_url = make_safe_url(url)
    return f"{safe_url}__{dt.strftime(TIME_FORMAT)}"

# ============================================================
# RESULT DIR CREATION
# ============================================================

def create_results_dir(url: str, mode: str = "normal", final_url: Optional[str] = None) -> Path:
    created = utc_now()
    results_id = make_results_id(url, created)
    results_dir = RESULTS_DIR / results_id
    results_dir.mkdir(parents=True, exist_ok=False)
    return results_dir

def normalize_html(html: str) -> str:
    return " ".join(html.split())


def hash_html(html: str) -> str:
    normalized = normalize_html(html)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def find_axe_violations(component_html: str, axe_data):
    if not axe_data:
        return []

    result = []
    comp_norm = normalize_html(component_html)

    for v in axe_data.get("violations", []):
        for node in v.get("nodes", []):
            node_html = normalize_html(node.get("html", ""))

            if comp_norm in node_html or node_html in comp_norm:
                result.append({
                    "rule_id": v.get("id"),
                    "impact": v.get("impact"),
                    "description": v.get("description")
                })

    return result

def suggest_types(query: str, existing_types):
    query = query.lower()

    all_types = set(BASE_TYPES) | set(existing_types)

    matches = [t for t in all_types if t.startswith(query)]

    return sorted(matches)


def input_multiline(prompt: str) -> str:
    print(prompt)
    print("(вставь HTML, затем Enter + пустая строка)")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def generate_id(counter: int) -> str:
    return f"cmp_{counter:04d}"

# Получаем тип компонента (по имени или номеру) - возможно пересмотреть логику так как создание нового типа тоже тут
def get_component_type(raw_type: str, type_list: List[str], components: Dict[str, List[Dict[str, Any]]]) -> str:
    # Если введён номер, преобразуем в индекс
    if raw_type.isdigit():
        idx = int(raw_type) - 1
        if 0 <= idx < len(type_list):
            return type_list[idx]
        else:
            print("❌ Неверный номер, попробуй снова.")
            return None

    # Если введён текст, ищем полное совпадение с существующими типами
    raw_type = raw_type.strip().lower()  # Приводим к нижнему регистру и очищаем от лишних пробелов
    
    # Если найдено полное совпадение
    if raw_type in type_list:
        return raw_type
    
    # Если текст не совпадает с существующими типами, показываем существующие типы и предлагаем новый ввод
    print("❗ Тип не найден среди существующих. Вот доступные типы:")
    for i, t in enumerate(type_list, start=1):
        print(f"{i} - {t}")
    
    # Запросить у пользователя новый тип
    new_type = input(f"Введите новый тип компонента (или выберите из списка): ").strip().lower()

    # Повторно проверяем, совпадает ли новый тип с существующими
    if new_type in type_list:
        return new_type
    else:
        # Предлагаем создать новый тип
        confirm = input(f"❗ Новый тип '{new_type}' не найден. Хотите создать новый тип? (y/n): ").strip().lower()
        if confirm == 'y':
            components[new_type] = []
            type_list.append(new_type)
            return new_type
        else:
            print("❌ Новый тип не создан, попробуйте снова.")
            return None


def main():
    print("=== Manual Component Annotation Tool ===")

    url = input("URL: ").strip()
    safe_name = make_safe_url(url)
    mode = input("Mode (normal/low_visual): ").strip() or "normal"
    result_dir = create_results_dir(url,mode)

    # 1. Формируем путь по умолчанию внутри result_dir
    default_axe = result_dir/"axe.json"
    print(f"💡 Сохрани axe в папку {result_dir} как: axe.json")  
    
    # 2. Предлагаем этот путь пользователю
    axe_path = Path(input(f"Путь к axe.json (Enter = {default_axe}): ").strip() or default_axe)

    # Проверка существования файла
    while not axe_path.exists():
        print(f"❌ Ошибка: Файл не найден по пути {axe_path}")
        axe_path = Path(input("Введите новый путь: ").strip())

    print(f"✅ Файл найден, продолжаем работу...")

    axe_data = None
    components = {}  # grouped by type
    html_index = {}  # hash → (type, id)
    type_list = BASE_TYPES   # для нумерации

    counter = 1

    if axe_path and axe_path.exists():
        with open(axe_path, "r", encoding="utf-8") as f:
            axe_data = json.load(f)
        print("✅ axe.json загружен")

    while True:
        print("\nДобавить компонент? (y/n)")
        value = input().strip().lower()
        while value not in ["y","n"]:
            print("\n❌ Неверный ввод. Введите 'y' для продолжения или 'n' для выхода.")
            value = input().strip().lower()

        if value == "n":
            break

        # показать список типов
        print("\nСуществующие типы:")
        for i, t in enumerate(type_list, start=1):
            print(f"{i} - {t}")
        
        component_type = None

        while not component_type:
            raw_type = input("\nТип компонента (название или номер): ").strip().lower()
            component_type = get_component_type(raw_type, type_list, components)

        html = input_multiline("HTML компонента:")
        h = hash_html(html)

        if h in html_index:
            existing_type, existing_id = html_index[h]
            print(f"⚠️ Дубликат найден: {existing_type} ({existing_id})")
            continue
        
        axe_violations = find_axe_violations(html, axe_data)

        component = {
            "id": generate_id(counter),
            "html": html,
            "manual": True,
            "axe": {
                "auto_links": axe_violations,
                "manual_impact": None
            }
        }

        components[component_type].append(component)
        counter += 1

    # сортировка типов по алфавиту
    sorted_components = dict(sorted(components.items(), key=lambda x: x[0]))

    data = {
        "url": url,
        "timestamp": datetime.utcnow().isoformat(),
        "mode": mode,
        "components": sorted_components
    }

    filename = f"{safe_name}_components.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Сохранено в {filename}")


if __name__ == "__main__":
    main()
