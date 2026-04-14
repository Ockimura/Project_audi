В данном проекте я реаллизую исследование доступности гос сайтов. Пока мой первый гит сырой, но постараюсь всё красиво оформить

# Логика аудита
'''
[Playwright аудит]
        ↓
JSON (на каждый URL)
        ↓
build_tables.py
        ↓
CSV таблицы (raw + pairs)
        ↓
a11y_analyze_from_tables_updated copy.py
        ↓
статистика + графики + отчёт
'''
# info_aggregation.py

## Пайплайн

1. читает URL,
1. запускает Chromium с WAVE,
1. открывает страницу,
1. ждёт ручной pause/resume,
1. считает DOM,
1. запускает axe,
1. создаёт manual-структуру,
1. ждёт ручной запуск WAVE,
1. сохраняет MHTML,
1. вытаскивает оттуда WAVE-метрики,
1. пишет JSON.

# build_tables.py

Это основной “сборщик таблиц” из JSON

Он делает:

1. Находит последний запуск results_YYYYMMDD_HHMMSS через: find_latest_results_dir()
2. Читает все JSON Из папки: results_xxx/json/
3. Извлекает ключевые метрики
4. Создаёт 2 таблицы: 
    + raw_pages.csv
    + pairs.csv, результаты аудита сайта сфгруппированы попарно normal_* low_*

# analyze_from_tables_updated.py
## Проверки
### H1 — сравнение manual vs axe
Берёт severity_mean(...) и переводит категории ошибок в числа

затем считает по ```wilcoxon_test(...)``` effect sixe (dz) и power

### H2 — структура (visible)

Сравнивает:

```normal vs low_vision```

по:

+ dom_total_visible
+ links_visible
+ buttons_visible
+ forms_visible
+ images_visible

### H3 — ошибки

Сравнивает:

```aim_score vs axe_ratio```


### H4 — корреляции

Пример: 
```dom_total_visible vs axe_nodes_count```
и:
```dom_total vs aim_score```

Вопрос: влияет ли размер DOM на количество ошибок?

## Результаты
### Графики

Создаёт:

+ scatter plot
+ histogram Δ
+ boxplot

### файлы в конце

Создаёт:

```
tables/
    raw_pages.csv
    pairs.csv
    figures/
    report.md
    h_tests.json
```
# Компоненты страниц

## Стартовый классификатор компонентов и ошибок

### Логика описания комопнентов страниц

```
DOM страницы
→ выделяем компоненты
→ у каждого компонента есть xpath / element_id / html
→ берем AXE ошибки
→ ищем, к какому компоненту относится ошибка
→ записываем ошибку внутрь этого компонента
→ WAVE добавляем так же
→ quality признаки считаем для этого же компонента
```

```
component
   ↓
DOM элемент
   ↓
quality признаки (наши правила)
   ↓
AXE ошибки (авто)
   ↓
WAVE ошибки (частично авто + вручную)
```
```JSON
{
  "site": "N",
  "components": {
    "button": {
      "count": 2,
      "axe": ["button-name", "color-contrast"],
      "wave": ["low_contrast"],
      "quality": ["icon_only", "pseudo_button"],
      "instances": [
        {
          "element_id": "el_001",
          "xpath": "/html/body/main/button[1]",
          "html": "<button><svg></svg></button>",
          "axe": ["button-name", "color-contrast"],
          "wave": ["low_contrast"],
          "quality": ["icon_only", "no_name"]
        },
        {
          "element_id": "el_002",
          "xpath": "/html/body/main/div[3]",
          "html": "<div onclick='open()'>...</div>",
          "axe": ["button-name"],
          "wave": [],
          "quality": ["pseudo_button"]
        }
      ]
    }
  }
}
```

### 1. Структурные компоненты

| Компонент | Что это                | Как распознавать                   |
| --------- | ---------------------- | ---------------------------------- |
| `header`  | верхняя часть страницы | `<header>` или первый крупный блок |
| `footer`  | нижняя часть           | `<footer>`                         |
| `nav`     | навигация              | `<nav>` или `role="navigation"`    |
| `main`    | основной контент       | `<main>`                           |
| `section` | смысловой блок         | `<section>`                        |
| `article` | отдельный контент      | `<article>`                        |
| `aside`   | боковая панель         | `<aside>`                          |

### 2. Контентные компоненты

| Компонент          | Что это             | Как распознавать                       |
| ------------------ | ------------------- | -------------------------------------- |
| `heading`          | заголовки           | `h1–h6`                                |
| `paragraph`        | текст               | `<p>`                                  |
| `list`             | списки              | `<ul>`, `<ol>`                         |
| `table`            | таблицы             | `<table>`                              |
| `image`            | изображения         | `<img>`                                |
| `background_image` | фоновые изображения | `background-image != none`             |
| `icon`             | иконки              | `<svg>` или `<img>` маленького размера |
| `document_link`    | ссылка на документ  | `<a href=".pdf/.doc/...">`             |

### 3. НАвигационные компоненты

| Компонент         | Что это                | Как распознавать                  |
| ----------------- | ---------------------- | --------------------------------- |
| `link`            | обычная ссылка         | `<a href>`                        |
| `navigation_link` | ссылка навигации       | `<nav> a`                         |
| `external_link`   | внешняя ссылка         | домен ≠ текущий                   |
| `breadcrumb`      | хлебные крошки         | список ссылок с `/`               |
| `pagination`      | постраничная навигация | номера страниц                    |
| `menu`            | меню                   | `<ul>` с ссылками                 |
| `submenu`         | вложенное меню         | `<ul>` внутри `<li>`              |
| `search`          | поиск                  | `input + button`, `role="search"` |


### 4. Интерактивные компоненты

| Компонент       | Что это           | Как распознавать                    |
| --------------- | ----------------- | ----------------------------------- |
| `button`        | кнопка            | `<button>`, `input[type=submit]`    |
| `pseudo_button` | “фейк-кнопка”     | `<div onclick>` / `<a>` без href    |
| `form`          | форма             | `<form>`                            |
| `form_control`  | поле ввода        | `<input>`, `<select>`, `<textarea>` |
| `input_text`    | текстовое поле    | `<input type="text">`               |
| `textarea`      | поле текста       | `<textarea>`                        |
| `select`        | выпадающий список | `<select>`                          |
| `checkbox`      | чекбокс           | `<input type="checkbox">`           |
| `radio`         | радио             | `<input type="radio">`              |

### 5. Составные (UI) компонеты

| Компонент      | Что это             | Как распознавать              |
| -------------- | ------------------- | ----------------------------- |
| `card`         | карточка            | блок: image + text + link     |
| `banner`       | баннер              | большой блок с изображением   |
| `hero`         | главный экран       | крупный верхний блок          |
| `alert`        | сообщение           | блок с текстом и стилем alert |
| `filter_panel` | фильтры             | form + select/checkbox        |
| `tabs`         | вкладки             | переключение контента         |
| `accordion`    | раскрывающийся блок | show/hide                     |
| `modal`        | модальное окно      | overlay                       |
| `dropdown`     | выпадающее меню     | скрытый список                |
| `tooltip`      | подсказка           | hover/aria-describedby        |

### 6. Специальные компоненты

| Компонент         | Что это         | Как распознавать             |
| ----------------- | --------------- | ---------------------------- |
| `image_link`      | картинка-ссылка | `<a><img></a>`               |
| `icon_button`     | кнопка-иконка   | button без текста            |
| `navigation_hint` | “подсказка”     | короткие ссылки типа "далее" |
| `content_block`   | текстовый блок  | section/article + текст      |


## Привязываем AXE к компонетам (автоматически)

### Собираем компоненты

```python
components = [
    {
        "element_id": "el_001",
        "component_type": "button",
        "xpath": "/html/body/main/button[1]",
        "html": "<button>...</button>"
    },
    {
        "element_id": "el_002",
        "component_type": "button",
        "xpath": "/html/body/main/div[3]",
        "html": "<div onclick='save()'>...</div>"
    }
]
```
### Берём AXE ошибки

```python
axe_errors = [
    {
        "rule": "button-name",
        "target": "/html/body/main/button[1]"
    },
    {
        "rule": "color-contrast",
        "target": "/html/body/main/button[1]/span[1]"
    }
]
```
### Ищём, к какому компоненту принадлежит target

Ошибка может быть:
* на самом компоненте
* на его дочернем узле
Поэтому проверка должна быть не только по ```zpeth == target```, но и:
```python
target.startsitch(component_xpath)
```
Например:
* компонент: ```/html/body/main/button[1]```
* ошибка: ```/html/body/main/button[1]/span[1]```

Это всё равно ошибка этого ```button```.

### Логика привязки

```python
def attach_axe_errors_to_components(components, axe_errors):
    for component in components:
        component["axe"] = []

    for error in axe_errors:
        target = error.get("target", "")
        for component in components:
            comp_xpath = component["xpath"]
            if target == comp_xpath or target.startswith(comp_xpath + "/"):
                component["axe"].append(error["rule"])
                break

    return components
```

## Привязываем WAVE к компонетам
У WAVE нет сопоставления по Xpath, поэтому вручную сопоставляем компонент с связанной ошибкой

## Программа для описания компонентов

Для процесса аудита страниц по компонентам и ошибкам в качестве помощника разработана мини-программа, которая помогвает обрабатывать данные. Потому что нужно не просто собрать DOM, а сделать полуавтоматическую разметку:

* открыть страницу;
* показать найденные компоненты;
* дать выбрать тип и подтип;
* отметить пропущенные компоненты;
* показать AXE/WAVE рядом;
* вручную привязать ошибки к компоненту;
* сохранить результат в JSON.

Ткой тип программ называют annotation tool. 
> annotation tool (инструмент разметки) — это программа, в которой вы вручную помечаете и описываете данные.

### Требования к программе 

#### 1. Просмотр старницы
Слева или в центре:
* открытая страница;
* возможность кликнуть по элементу;
* подсветка выбранного DOM-узла;
* желательно режим hover, чтобы видеть границы.

#### 2. Панель компонентов
Справа:
+ список всех автоматически найденных компонентов;
+ фильтр по типу: ```button```, ```link```, ```image```, ```form_control``` и так далее;
+ статус:
    + auto_found
    + manual_added
    + verified
    + needs_review

Для каждого компонента:
+ тип;
+ подтип;
+ xpath;
+ html;
+ текст;
+ признаки качества;
+ связанные ошибки.

#### 3. Панель ошибок

Отдельный блок:

+ AXE ошибки по странице;
+ WAVE ошибки по странице;
+ какие уже привязаны;
+ какие ещё не привязаны.

Очень важно иметь две кнопки:

+ ```attach to selected component```
+ ```mark as global```

Потому что не все ошибки честно привязываются к одному элементу.

#### 4. Панель ручной равзметки

Для выбранного элемента:

+ выбрать ```component_type```;
+ выбрать ```semantic_subtype```;
+ ввести ручные quality-признаки;
+ отметить:
    + ```component missed by auto detection```
    + ```wrong auto type```
    + ```wave linked manually```

## Как должен идти рабочий процесс

### Шаг 1

Программа открывает URL

### Шаг 2

Автоматически:

+ собирает DOM;
+ строит список кандидатов в компоненты;
+ запускает AXE;
+ подтягивает WAVE-результаты, если есть.

### Шаг 3

Ручной проход страницы:

+ проверка, все ли компоненты найдены;
+ добавление пропущенных
+ уточнение, исправление типизаци;
+ добавление ошибок

### Шаг 4

Сохранение размеченного JSON.

## MVP программы 0 

Максимально простой вариант Python + браузер, интерфейс запускается как обычная веб страница

1. Python backend
2. HTML - страница
3. Кнопка:
    + "Открыть страницу"
    + вызывает Playwright
    + получает данные
    + показывает их