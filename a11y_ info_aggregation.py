import json
import hashlib
import asyncio
import shutil
import quopri
import re

from pathlib import Path
from datetime import datetime, timedelta, timezone
from tqdm.auto import tqdm
from lxml import html
from playwright.async_api import async_playwright, TimeoutError, Error as PlaywrightError


# =====================================================
# CONFIG
# =====================================================
URLS_FILE = "urls_have_low.txt"
OUTPUT_DIR = Path("results")
JSON_DIR = OUTPUT_DIR / "json"
MHTML_DIR = OUTPUT_DIR / "mhtml"

profile_dir = Path("./profile")
profile_dir.mkdir(exist_ok=True)

JSON_DIR.mkdir(parents=True, exist_ok=True)
MHTML_DIR.mkdir(parents=True, exist_ok=True)

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

# =====================================================
# UTILS
# =====================================================
def md5_hash(s): return hashlib.md5(s.encode()).hexdigest()[:8]
def sha1_hash(s): return hashlib.sha1(s.encode()).hexdigest()
def element_uid(xpath): return "el_" + sha1_hash(xpath)[:6]

def should_skip(url):
    path = JSON_DIR / f"{md5_hash(url)}_{MODE}.json"
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if datetime.now(timezone.utc) - mtime < timedelta(hours=4):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("navigation_error"):
                return True
    return False

def axe_node_uid(rule_id, impact, html_snippet, targets):
    flat_targets = [str(t) for t in targets]
    base = (
        rule_id + "|" +
        (impact or "") + "|" +
        (html_snippet or "").replace("\n", " ").strip() + "|" +
        "|".join(sorted(flat_targets))
    )
    return "axe_" + sha1_hash(base)[:12]

async def get_visibility_safe(page, all_elements, tree):
    """
    Пытаемся вычислить точную видимость через JS.
    Если не получается — fallback на базовую проверку через style.
    """
    xpaths = [tree.getpath(el) for el in all_elements]

    try:
        # Попытка JS
        visibility = await compute_visibility(page, xpaths)
    except Exception:
        # fallback: style + lxml
        visibility = {}
        for el, xp in zip(all_elements, xpaths):
            style = el.attrib.get("style", "")
            visibility[xp] = "display:none" not in style and "visibility:hidden" not in style

    return visibility

async def compute_visibility(page, xpaths):
    """
    Проверяет видимость каждого xpath на странице.
    Использует computedStyle, offsetParent, getClientRects.
    Возвращает словарь {xpath: True/False}.
    """
    script = """
    (xpaths) => {
        const results = {};
        for (const xp of xpaths) {
            try {
                const el = document.evaluate(
                    xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
                ).singleNodeValue;
                if (!el) { results[xp] = false; continue; }
                const style = window.getComputedStyle(el);
                const visible = (
                    style &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    el.offsetParent !== null &&
                    el.getClientRects().length > 0
                );
                results[xp] = visible;
            } catch(e) { results[xp] = false; }
        }
        return results;
    }
    """
    return await page.evaluate(script, xpaths)

def extract_wave_from_mhtml(mhtml_data):
    metrics = {
        "Wave_Error": 0,
        "Wave_Contrast_Error": 0,
        "Wave_Alerts": 0,
        "Wave_Features": 0,
        "Wave_Structure": 0,
        "Wave_Aria": 0,
        "aim_score": None
    }
    mapping = {
        "Wave_Error": r'id="error"[^>]*>(\d+)',
        "Wave_Contrast_Error": r'id="contrastnum"[^>]*>(\d+)',
        "Wave_Alerts": r'id="alert"[^>]*>(\d+)',
        "Wave_Features": r'id="feature"[^>]*>(\d+)',
        "Wave_Structure": r'id="structure"[^>]*>(\d+)',
        "Wave_Aria": r'id="aria"[^>]*>(\d+)',
        "aim_score": r'id="aim_score"[^>]*>(\d+)'
    }
    for key, pattern in mapping.items():
        m = re.search(pattern, mhtml_data)
        if m:
            metrics[key] = int(m.group(1))
    return metrics

async def page_goto(page, url, retries=max_retries):
    for i in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return True, None
        except  (TimeoutError, PlaywrightError) as e:
            if i == retries - 1:
                return False, str(e)
            print(f"  [Retry {i}] Ошибка: {url}. Пробуем снова...")
        await asyncio.sleep(2)
    return False, "Неизвестная ошибка при навигации"

def clean_mhtml_content(data):
    # Декодируем Quoted-Printable (превращает 3D= в =)
    decoded = quopri.decodestring(data).decode('utf-8', errors='ignore')
    # Убираем мягкие переносы строк, которые MHTML вставляет каждые 76 символов
    return decoded.replace('=\n', '').replace('=\r\n', '')

async def get_stable_page_content(page, retries=5, delay=0.5):
    """
    Безопасно получает HTML страницы в условиях SPA и post-pause.
    """
    last_error = None

    for attempt in range(retries):
        try:
            # Если вдруг идёт навигация — ждём
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=5000)

            # Даём SPA дорисоваться
            await asyncio.sleep(delay)

            return await page.content()

        except PlaywrightError as e:
            last_error = e
            await asyncio.sleep(delay)

    raise PlaywrightError(
        f"Не удалось получить стабильный DOM после {retries} попыток: {last_error}"
    )

# =====================================================
# PAGE ANALYSIS
# =====================================================
async def analyze_page(page, url):

    result = {
        "meta": {
            "start_url": url,
            "final_url": None,
            "version": MODE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "navigation_error": None,
        "page_crashed": None, 
        "spa_suspected": None,
        "dom": {},
        "excluded": {}, #пока не реализован
        "elements": [],
        "axe": {"violations_count": 0, "nodes_count": 0, "nodes": []},
        "cdp": {"snapshot_saved": False, "error": None},
        "wave": {},
    }
        
    # 1. Пытаемся зайти на сайт
    success, nav_error = await page_goto(page, url)
    if not success:
        result["navigation_error"] = nav_error
        return result
        
    result["meta"]["final_url"] = page.url

    # 2. Интерактивная пауза: готовим страницу в Инспекторе
    await page.pause()
    user_input = input("Команда (Enter/error): ").strip().lower()

    if page.is_closed() :
        result["navigation_error"] = "inspector_closed_manually"
        return result

    if user_input == "error":
        result["navigation_error"] = "forced_skip_by_user"
        print(f"⚠️  Пропуск сайта {url} по команде пользователя.")
        return result
    
    # 3. После нажатия Resume мгновенно забираем код
    # Если за время паузы страница закрылась, обработаем это
    try:
        #======================================
        # DOM 
        #======================================
        html_source = await get_stable_page_content(page)
        root = html.fromstring(html_source)
        tree = root.getroottree()

        # 2. Собираем данные в один проход

        all_elements = root.xpath("//*")

        xpaths = [tree.getpath(el) for el in all_elements]

        visibility = await get_visibility_safe(page, all_elements, tree)
        '''
        Это генератор словаря (dict comprehension). Эта короткая строчка заменяет собой длинный цикл создания словаря со счетчиками.
        Разберем ее «на запчасти»:
        1. for g in TAG_GROUPS: Проходит по всем названиям групп из вашего конфига ("images", "links", "h1" и т.д.).
        2. for v in ["", "_visible"]: Для каждой группы подставляет по очереди пустое окончание и окончание _visible.
        3. f"{g}{v}": Создает имя ключа, склеивая название группы и окончание.
            3.1 Для группы images получится: images и images_visible.
            3.2 Для группы links получится: links и links_visible.
        4.  : 0: Каждому созданному ключу присваивает начальное значение 0.
        '''
        elements = []
        dom_visible = {f"{g}{v}": 0 for g in TAG_GROUPS for v in ["", "_visible"]}

        for el, xp in zip(all_elements, xpaths):
            tag = el.tag.lower()
            visible = visibility.get(xp, False)
            group = tag_map.get(tag)
            if group:
                dom_visible[group] += 1
                if visible:
                    dom_visible[f"{group}_visible"] += 1
            elements.append({
                "element_id": element_uid(xp),
                "tag": tag,
                "xpath": xp,
                "visible": visible
    })
            
        result["dom"] = {**dom_visible, "dom_total": len(all_elements)}
        result["elements"] = elements

        #======================================
        # AXE
        #======================================
        try:
            await page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js")
            axe_results = await page.evaluate("axe.run({ resultTypes: ['violations'], selectors: true, xpath: true })")
        except Exception:
            axe_results = {"violations": []}

        impact_summary = {k: 0 for k in ["critical", "serious", "moderate", "minor"]}
        axe_nodes = []

        for v in axe_results.get("violations", []):
            for n in v.get("nodes", []):
                impact = v.get("impact")
                if impact in impact_summary:
                    impact_summary[impact] += 1
                uid = axe_node_uid(v["id"], v.get("impact"), n.get("html", ""), n.get("target", []))
                axe_nodes.append({
                    "axe_node_uid": uid,
                    "rule_id": v["id"],
                    "impact": impact,
                    "wcag": v.get("tags", []),
                    "description": v.get("description"),
                    "help": v.get("help"),
                    "help_url": v.get("helpUrl"),
                    "targets": n.get("target", []),
                    "html": n.get("html", ""),
                    "manual_impact": None
                })
                result["axe"]["impact_summary"] = impact_summary

        result["axe"] = {
            "violations_count": len(axe_results.get("violations", [])),
            "nodes_count": len(axe_nodes),
            "impact_summary": impact_summary,
            "nodes": axe_nodes
        }

        #======================================
        # WAVE
        #======================================
        tqdm.write(f"--- Запустите WAVE на {page.url} и нажмите Resume в Inspector ---")
        await page.pause() 

        # В асинхронной среде input() блокирует поток, 
        # но после завершения page.pause() это допустимо.
        user_input = input("Команда (Enter/error): ").strip().lower()

        if page.is_closed() :
            result["navigation_error"] = "inspector_closed_manually"
            return result

        if user_input == "error":
            result["navigation_error"] = "forced_skip_by_user"
            print(f"⚠️  Пропуск сайта {url} по команде пользователя.")
            return result

        try:
            client = await page.context.new_cdp_session(page)
            snapshot = await client.send(
                "Page.captureSnapshot",
                {"format": "mhtml"}
            )
            result["cdp"]["snapshot_saved"] = True

            mhtml_raw_data = snapshot["data"]

            mhtml_path = MHTML_DIR / f"{md5_hash(url)}_wave_{MODE}.mhtml"
            mhtml_path.write_text(snapshot["data"], encoding="utf-8", errors="ignore", newline="\n")

            # 4. ДЕКОДИРОВАНИЕ И ПОИСК МЕТРИК # Декодируем специфическую кодировку MHTML (=3D -> =)
            decoded_text = clean_mhtml_content(mhtml_raw_data)
            wave_metrics = extract_wave_from_mhtml(decoded_text)

            # 5. ОБРАБОТКА РЕЗУЛЬТАТОВ
            # Исключаем aim_score из проверки на "пустоту", так как он может быть None легально
            check_values = [v for k, v in wave_metrics.items() if k != "aim_score"]
            
            if all(v == 0 for v in check_values):
                result["wave"] = {
                    "status": "error",
                    "error_reason": "no_wave_markers_found_in_mhtml",
                    "metrics": wave_metrics
                }
                result["meta"]["wave_status"] = "not_detected"
            else:
                result["wave"] = {
                    "status": "ok",
                    "error_reason": None,
                    "metrics": wave_metrics
                }
                result["meta"]["wave_status"] = "ok"

            result["meta"]["aim_score"] = wave_metrics.get("aim_score")

        except Exception as e:
            result["cdp"]["error"] = str(e)

    except Exception as e:
        result["navigation_error"] = f"lost_after_pause: {e}"
        return result

# =====================================================
# RUNNER
# =====================================================

async def main():

    urls = [l.strip() for l in Path(URLS_FILE).read_text().splitlines() if l.strip()]
    context = None
	# Удаляем старые профили запуска браузера, если они есть
    
    profile_dir = Path(f"./profile")
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)

    # такое обьявление обязательно. new_context() не создаёт экземпляр браузера самостоятельно
    #async with async_playwright() as p:
    #    browser = await p.chromium.launch(headless=False)
    #    context = await browser.new_context()

    #В Playwright архитектура такая:
    #p.chromium.launch() — создает экземпляр браузера (процесс). У него нет своей папки профиля по умолчанию для расширений.
    #launch_persistent_context() — это самостоятельный метод. Он сам запускает браузер и сразу привязывает его к папке. 
    #У объекта browser (который вы получили из launch()) нет метода launch_persistent_context. 
    # WAVE и другие расширения не работают в обычном browser.new_context(). Только через launch_persistent_context.
    
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                ignore_https_errors=True,
                args=[
                    f"--disable-extensions-except={WAVE_EXTENSION_PATH}",
                    f"--load-extension={WAVE_EXTENSION_PATH}"
                ]
            )
            
            check = await context.new_page()
            await check.goto("chrome://extensions/", wait_until="load")
            input("\n--- Убедитесь, что WAVE включён, Enter ---\n")
            await check.close()
    

            page = await context.new_page()
            '''
            urls: ваш список адресов сайтов из файла.
            enumerate(..., 1): эта функция «нумерует» список.
            Цифра 1 говорит о том, что отсчет нужно начинать с единицы (а не с нуля, как обычно в программировании).
            i: переменная, в которую записывается текущий номер (1, 2, 3...).
            url: переменная, в которую записывается текущий адрес сайта.
            '''
            for i, url in enumerate(tqdm(urls, desc="Анализ сайтов"), 1):
                if should_skip(url):
                    tqdm.write(f"⏭ Пропуск {url}")
                    continue

                tqdm.write(f"[{i}/{len(urls)}] {url}")
                data = await analyze_page(page, url)

                out = JSON_DIR / f"{md5_hash(url)}_{MODE}.json"
                out.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                tqdm.write(f"✅ {out.name}")

        finally:
            # Гарантированное закрытие и очистка
            if context is not None:
                await context.close()
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
                tqdm.write(f"🧹 Временный профиль {profile_dir.name} удален.")