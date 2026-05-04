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
from datetime import datetime
import hashlib
import re
import os

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

def make_safe_url_name(url: str) -> str:
    # убираем протокол
    url = re.sub(r'^https?://', '', url)

    # заменяем всё странное на _
    safe = re.sub(r'[^a-zA-Z0-9]', '_', url)

    # хеш (короткий)
    h = hashlib.md5(url.encode()).hexdigest()[:6]

    return f"{safe}_{h}"


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


def main():
    print("=== Manual Component Annotation Tool ===")

    url = input("URL: ").strip()
    safe_name = make_safe_url_name(url)+f"{int(datetime.utcnow().timestamp())}"
    print(f"💡 Сохрани axe как: {safe_name}_axe.json")
    mode = input("Mode (normal/low_visual): ").strip() or "normal"

    axe_data = None
    default_axe = f"{safe_name}_axe.json"
    axe_path = input(f"Путь к axe.json (Enter = {default_axe}): ").strip()

    components = {}  # grouped by type
    html_index = {}  # hash → (type, id)
    type_list = []   # для нумерации

    counter = 1

    if not axe_path:
        axe_path = default_axe

    if axe_path and os.path.exists(axe_path):
        with open(axe_path, "r", encoding="utf-8") as f:
            axe_data = json.load(f)
        print("✅ axe.json загружен")

    while True:

        print("\nДобавить компонент? (y/n)")
        if input().strip().lower() != "y":
            break

        # показать список типов
        if type_list:
            print("\nСуществующие типы:")
            for i, t in enumerate(type_list, start=1):
                print(f"{i} - {t}")

        raw_type = input("\nТип компонента (название или номер): ").strip().lower()

        # если ввод текстом — показать подсказки
        if not raw_type.isdigit():
            suggestions = suggest_types(raw_type, type_list)
            if len(suggestions) == 1:
                auto = suggestions[0]
                print(f"👉 Автовыбор: {auto}")
                raw_type = auto
            elif suggestions:
                print("Подсказки:", ", ".join(suggestions))

        # если введён номер
        if raw_type.isdigit():
            idx = int(raw_type) - 1
            if 0 <= idx < len(type_list):
                component_type = type_list[idx]
            else:
                print("❌ Неверный номер, попробуй снова")
                continue
        else:
            component_type = raw_type.lower()
            if component_type not in components:
                components[component_type] = []
                type_list.append(component_type)

        html = input_multiline("HTML компонента:")
        h = hash_html(html)

        if h in html_index:
            existing_type, existing_id = html_index[h]
            print(f"⚠️ Дубликат найден: {existing_type} ({existing_id})")

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
