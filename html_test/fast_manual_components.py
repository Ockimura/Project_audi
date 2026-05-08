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

# ============================================================
# КОМПОНЕНТЫ ВСПОМОГАТЕЛЬНЫЕ ДЛЯ РАБОТЫ main
# ============================================================

def choose_action() -> str:
    print("Выберите действие:")
    print("1 - продолжить заполнение файла")
    print("2 - исследовать новый URL")

    while True:
        action = input("Действие: ").strip()

        if action in ["1", "2"]:
            return action

        print("❌ Неверный ввод. Введите 1 или 2.")



# ============================================================
# РАБОТА С ТИПАМИ, ДУМАЮ НУЖНО ВВЕСТИ КЛАССЫ
# ============================================================        

def get_components_count(components: Dict[str, List[Dict[str, Any]]]) -> int:
    return sum(len(items) for items in components.values())


def get_next_counter(components: Dict[str, List[Dict[str, Any]]]) -> int:
    """
    Ищет последний id вида cmp_0001, cmp_0002 и возвращает следующий номер.
    """
    max_id = 0
    pattern = re.compile(r"^cmp_(\d+)$")

    for component_list in components.values():
        for component in component_list:
            component_id = component.get("id", "")
            match = pattern.match(component_id)

            if match:
                max_id = max(max_id, int(match.group(1)))

    return max_id + 1


def build_html_index(components: Dict[str, List[Dict[str, Any]]]) -> Dict[str, tuple]:
    """
    Восстанавливает индекс html-хэшей после загрузки существующего JSON.
    Нужен, чтобы дубликаты находились и после продолжения работы.
    """
    html_index = {}

    for component_type, component_list in components.items():
        for component in component_list:
            html = component.get("html", "")
            component_id = component.get("id", "")

            if html and component_id:
                html_index[hash_html(html)] = (component_type, component_id)

    return html_index


def build_type_list(components: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """
    Базовые типы + пользовательские типы из уже заполненного JSON.
    """
    type_list = list(BASE_TYPES)

    for component_type in components.keys():
        if component_type not in type_list:
            type_list.append(component_type)

    return type_list


def save_components_json(
    json_path: Path,
    url: str,
    mode: str,
    components: Dict[str, List[Dict[str, Any]]]
) -> None:
    """
    Сохраняет JSON после каждого добавления компонента.
    """
    sorted_components = dict(sorted(components.items(), key=lambda x: x[0]))

    data = {
        "url": url,
        "timestamp": datetime.utcnow().isoformat(),
        "mode": mode,
        "components": sorted_components
    }

    temp_path = json_path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(temp_path, json_path)


def find_existing_json(url: str) -> Optional[Path]:
    """
    Ищет основной JSON-файл по URL.
    Сначала ищет рядом со скриптом, затем внутри RESULTS_DIR.
    """
    safe_name = make_safe_url(url)

    direct_path = Path(f"{safe_name}_components.json")

    if direct_path.exists():
        return direct_path

    if RESULTS_DIR.exists():
        matches = list(RESULTS_DIR.glob(f"**/{safe_name}_components.json"))

        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]

    return None


def find_latest_results_dir(url: str) -> Optional[Path]:
    """
    Ищет последнюю папку аудита по URL.
    Нужно, чтобы предложить путь к axe.json при продолжении.
    """
    safe_name = make_safe_url(url)

    if not RESULTS_DIR.exists():
        return None

    matches = [
        path for path in RESULTS_DIR.glob(f"{safe_name}__*")
        if path.is_dir()
    ]

    if not matches:
        return None

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    return matches[0]


def load_existing_components_json(json_path: Path) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_axe_json(default_axe: Path) -> Dict[str, Any]:
    print(f"💡 Путь к axe.json по умолчанию: {default_axe}")

    axe_path = Path(
        input(f"Путь к axe.json (Enter = {default_axe}): ").strip()
        or default_axe
    )

    while not axe_path.exists():
        print(f"❌ Ошибка: Файл не найден по пути {axe_path}")
        axe_path = Path(input("Введите новый путь: ").strip())

    with open(axe_path, "r", encoding="utf-8") as f:
        axe_data = json.load(f)

    print("✅ axe.json загружен")

    return axe_data


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
            return ""

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
            return ""


def main():
    print("=== Manual Component Annotation Tool ===")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    action = choose_action()

    # ============================================================
    # 1 - ПРОДОЛЖИТЬ ЗАПОЛНЕНИЕ ФАЙЛА
    # ============================================================

    if action == "1":
        url = input("URL: ").strip()
        safe_name = make_safe_url(url)

        json_path = find_existing_json(url)

        while not json_path:
            print(f"❌ JSON-файл для URL не найден: {safe_name}_components.json")
            manual_path = input("Введите путь к JSON-файлу вручную: ").strip()

            if manual_path and Path(manual_path).exists():
                json_path = Path(manual_path)
            else:
                print("❌ Файл не найден.")

        data = load_existing_components_json(json_path)

        url = data.get("url", url)
        mode = data.get("mode", "normal")
        components = data.get("components", {})

        counter = get_next_counter(components)
        html_index = build_html_index(components)
        type_list = build_type_list(components)

        latest_results_dir = find_latest_results_dir(url)

        if latest_results_dir:
            default_axe = latest_results_dir / "axe.json"
        else:
            default_axe = Path("axe.json")

        axe_data = load_axe_json(default_axe)

        print("\n✅ Продолжаем заполнение файла")
        print(f"Файл: {json_path}")
        print(f"Компонентов уже заполнено: {get_components_count(components)}")
        print(f"Следующий id: {generate_id(counter)}")

    # ============================================================
    # 2 - ИССЛЕДОВАТЬ НОВЫЙ URL
    # ============================================================

    else:
        url = input("URL: ").strip()
        safe_name = make_safe_url(url)

        mode = input("Mode (normal/low_visual): ").strip() or "normal"

        result_dir = create_results_dir(url, mode)
        default_axe = result_dir / "axe.json"

        print(f"💡 Сохрани axe в папку {result_dir} как: axe.json")

        axe_data = load_axe_json(default_axe)

        components = {}
        html_index = {}
        type_list = list(BASE_TYPES)
        counter = 1

        json_path = Path(f"{safe_name}_components.json")

        print("\n✅ Начинаем новый аудит")
        print(f"Файл сохранения: {json_path}")

    # ============================================================
    # ОБЩАЯ ЛОГИКА ВВОДА КОМПОНЕНТОВ
    # ============================================================

    while True:
        print("\nДобавить компонент? (y/n)")
        value = input().strip().lower()

        while value not in ["y", "n"]:
            print("\n❌ Неверный ввод. Введите 'y' для продолжения или 'n' для выхода.")
            value = input().strip().lower()

        if value == "n":
            break

        print("\nСуществующие типы:")
        for i, t in enumerate(type_list, start=1):
            print(f"{i} - {t}")

        component_type = ""

        while component_type == "":
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

        components.setdefault(component_type, []).append(component)
        html_index[h] = (component_type, component["id"])

        counter += 1

        save_components_json(
            json_path=json_path,
            url=url,
            mode=mode,
            components=components
        )

        print(f"💾 Сохранено обновление в файл: {json_path}")

    save_components_json(
        json_path=json_path,
        url=url,
        mode=mode,
        components=components
    )

    print(f"\n✅ Работа завершена")
    print(f"✅ Итоговый файл: {json_path}")
    print(f"✅ Всего компонентов: {get_components_count(components)}")