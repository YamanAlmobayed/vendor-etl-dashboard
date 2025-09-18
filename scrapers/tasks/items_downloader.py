import os
import threading
import pandas as pd
import shutil
import logging
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from celery import shared_task
import traceback
import psutil
from utils import image_downloader
import utils.sanitizers as sanitizer
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
import random
from pathlib import Path

from utils.excel_handlers import append_to_excel, merge_excels
from utils.url_formatters import deliveroo_url_formatter, talabat_url_formatter, noon_url_formatter
from utils.file_cleanup_manager import FileCleanupManager, cleanup_task_files

logger = logging.getLogger(__name__)
lock = threading.Lock()

def detect_parser_from_url(url):
    """Automatically detect parser type from URL"""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    
    if 'deliveroo' in domain:
        return 'deliveroo'
    elif 'instashop' in domain:
        return 'instashop'
    elif 'noon' in domain:
        return 'noon'
    elif 'talabat' in domain:
        return 'talabat'
    else:
        return None

def get_parser_info(url, menu_type):
    """Detect platform and return appropriate parser, selector, and formatter."""
    if "deliveroo" in url:
        return {
            "parser": "deliveroo",
            "items_selector": {
                'single': {'selector': "div.Layout-4549ebf43c78c99a"},
                'multi': {'selector': "div.MenuItemCard-a927b3314fc88b17.MenuItemCard-1c229a9ab5bb702f"}
            }[menu_type],
            "url_formatter": deliveroo_url_formatter,
            "scroll_type": "slow"
        }

    elif "talabat" in url:
        return {
            "parser": "talabat",
            "items_selector": {
                'single': {'selector': "div[data-testid='menu-category']"},
                'multi': {'selector': None}
            }[menu_type],
            "url_formatter": talabat_url_formatter,
            "scroll_type": "slow"
        }

    elif "instashop" in url:
        return {
            "parser": "instashop",
            "items_selector": {
                'single': {'selector': None},
                'multi': {'selector': "div.product.ng-star-inserted"}
            }[menu_type],
            "url_formatter": None,
            "scroll_type": "fast"
        }
    
    elif "noon" in url:
        return {
            "parser": "noon",
            "items_selector": {
                'single': {'selector': None},
                'multi': {'selector': "a[aria-label^='View product details for']"}
            }[menu_type],
            "url_formatter": noon_url_formatter,
            "scroll_type": "slow"
        }
    else:
        raise ValueError(f"Unsupported platform: {url}")

def create_vendor_zip(scrapped_dir, vendor_name):
    """Create zip file of vendor data."""
    try:
        zip_path = os.path.join(scrapped_dir, vendor_name)
        shutil.make_archive(zip_path, 'zip', zip_path)
        shutil.rmtree(zip_path)
        return {
            'status': 'completed',
            'message': f'{vendor_name} have been downloaded successfully',
            'download_link': f'http://10.39.1.71/{zip_path}.zip'
        }
    except Exception as e:
        logger.error(f"Error creating zip file: {e}")
        raise

def read_categories_from_excel(file_path): 
    """Read Excel rows as a list of dictionaries."""
    df = pd.read_excel(file_path)
    return df.to_dict(orient='records')

def scrape_category(data, report_path, images_path, task_id):
    """Scrape one category in a multi-menu layout (used in threading)."""
    url = data.get('url')
    category = data.get('Category')

    if not url or not category:
        logger.warning(f"Skipping invalid row: {data}")
        return

    try:
        config = get_parser_info(url, menu_type='multi')

        downloader = ItemsDownloader(
            menu_url=url,
            menu_type='multi',
            category=category,
            parser=config["parser"],
            items_list=config["items_selector"],
            url_formatter=config["url_formatter"],
            report_path=report_path,
            downloaded_images_path=images_path,
            is_headless=True,
            scroll_type=config["scroll_type"]
        )

        downloader.start()

        with lock:
            for item in downloader.menu:
                append_to_excel(
                    os.path.join(report_path, f"{downloader.category}.xlsx"),
                    excel_attributes=item
                )

    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        raise

@shared_task(bind=True, name="items_scraper")
def scrape_menu_items(self, menu_url, vendor_name, menu_type='single', excel_file=None):
    """Celery task to scrape menu items from various providers."""
    task_id = self.request.id
    
    try:
        base_dir = Path('scrapped_data')
        vendor_dir = os.path.join(base_dir, vendor_name)
        reports_path = os.path.join(vendor_dir, "reports")
        images_path = os.path.join(vendor_dir, "images")
        
        os.makedirs(reports_path, exist_ok=True)
        os.makedirs(images_path, exist_ok=True)
        
        if menu_type == 'single':
            FileCleanupManager.register_files_for_cleanup(task_id, [vendor_dir])
            detected_parser = detect_parser_from_url(menu_url)
            if not detected_parser:
                raise ValueError(f"Unable to determine parser for URL: {menu_url}")

            self.update_state(
                state='PROGRESS',
                meta={
                    'message': f'Starting to scrape {menu_type} menu for {vendor_name}',
                    'vendor_name': vendor_name,
                    'parser': detected_parser
                }
            )
            config = get_parser_info(menu_url, menu_type='single')
            
            downloader = ItemsDownloader(
                menu_url=menu_url,
                menu_type='single',
                category='',
                parser=config["parser"],
                items_list=config["items_selector"],
                url_formatter=config["url_formatter"],
                report_path=reports_path,
                downloaded_images_path=images_path,
                scroll_type=config["scroll_type"],
                is_headless=True
            )
            
            self.update_state(
                state='PROGRESS',
                meta={'message': f'Scraping single menu from {menu_url}'}
            )
            
            downloader.start()
            
            for item in downloader.menu:
                try:
                    append_to_excel(
                        os.path.join(reports_path, "report.xlsx"),
                        excel_attributes=item
                    )
                except Exception as e:
                    logger.warning(f"Error saving item to Excel: {e}")
                    
        elif menu_type == 'multi':
            if not excel_file:
                raise ValueError("Excel file content is required for multi mode")
            
            FileCleanupManager.register_files_for_cleanup(task_id, [vendor_dir, excel_file])
            
            categories = read_categories_from_excel(excel_file)
            
            detected_parser = detect_parser_from_url(categories[0]['url'])
            if not detected_parser:
                raise ValueError(f"Unable to determine parser for URL: {menu_url}")

            self.update_state(
                state='PROGRESS',
                meta={
                    'message': f'Starting to scrape {menu_type} menu for {vendor_name}',
                    'vendor_name': vendor_name,
                    'parser': detected_parser
                }
            )
            
            cpu_cores = os.cpu_count() or 4
            available_gb = psutil.virtual_memory().available / 1e9
            
            if available_gb < 2:
                max_workers = 1
            elif available_gb < 4:
                max_workers = min(2, cpu_cores)
            else:
                max_workers = min(4, cpu_cores)
            
            logger.info(f"Using {max_workers} concurrent threads for {len(categories)} categories")
            
            self.update_state(
                state='PROGRESS',
                meta={
                    'message': f'Scraping {len(categories)} categories using {max_workers} threads',
                    'total_categories': len(categories)
                }
            )
            
            def safe_scrape(row):
                scrape_category(row, reports_path, images_path, task_id)
            
            completed = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for row in categories:
                    future = executor.submit(safe_scrape, row)
                    futures.append(future)
                    time.sleep(2)
                
                for future in futures:
                    try:
                        future.result()
                        completed += 1
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'message': f'Completed {completed}/{len(categories)} categories',
                                'completed': completed,
                                'total': len(categories)
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error in thread execution: {e}")
                        completed += 1
            
            merge_excels(reports_path, os.path.join(reports_path, "report.xlsx"))
        
        else:
            raise ValueError(f"Invalid menu_type: {menu_type}. Must be 'single' or 'multi'")
        
        self.update_state(
            state='PROGRESS',
            meta={'message': f'Creating zip file for {vendor_name}'}
        )
        
        zip_result = create_vendor_zip(base_dir, vendor_name)
        
        cleanup_task_files.apply_async(args=[task_id], countdown=5)
        
        return {
            'status': 'completed',
            'download_link': zip_result['download_link'],
            'message': zip_result['message']
        }
        
    except Exception as e:
        self.update_state(
            state='FAILURE',
            meta={
                'error': str(e),
                'traceback': traceback.format_exc(),
                'vendor_name': vendor_name
            }
        )
        raise
    

class MenuItem:
    def __init__(self, title, price, description, image_url, category, image_file_name):
        self.title = title
        self.price = price
        self.description = description
        self.image_url = image_url
        self.category = category
        self.image_file_name = image_file_name

    def to_dict(self):
        return {
            "CategoryName": self.category,
            "title": self.title,
            "price": self.price,
            "description": self.description,
            "image": self.image_url,
            "Image_Status": "" if self.image_url else "No Image",
            "Image_File_Name": self.image_file_name
        }

class DeliverooParser:
    def __init__(self, ctx):
        self.ctx = ctx

    def parse(self, soup):
        if self.ctx.menu_type == "single":
            categories = soup.select(self.ctx.items_selector["selector"])[1:]
            for order, category in enumerate(categories, start=1):
                self.ctx.category = category.select_one("h2").text.strip()
                append_to_excel(
                    os.path.join(self.ctx.report_path, "cat.xlsx"),
                    excel_attributes={"CategoryName": self.ctx.category, "DisplayOrder": order}
                )
                items = category.select("div.MenuItemCard-a927b3314fc88b17")
                self._parse_items(items)
        elif self.ctx.menu_type == "multi":
            items = soup.select(self.ctx.items_selector["selector"])
            self._parse_items(items)
        else:
            logging.warning("Invalid menu type for Deliveroo")

    def _parse_items(self, items):
        for item in items:
            try:
                title_tag = item.select_one("span.MenuItemCard-d6db654c8b51be41.MenuItemCard-a7b546f3e376be7f") or \
                            item.select_one("p.ccl-649204f2a8e630fd")
                title = sanitizer.sanitize_text(title_tag.text.strip()) if title_tag else ""

                try:
                    price = item.select_one("span.ccl-649204f2a8e630fd.ccl-6f43f9bb8ff2d712.ccl-32ec9a3197735a65.ccl-08c109442f3e666d").text.split()[1]
                except:
                    price = ""

                try:
                    description = None
                    candidates = item.find_all("span", class_="ccl-649204f2a8e630fd")

                    for candidate in candidates:
                        classes = candidate.get("class", [])
                        if set(classes) == set(["ccl-649204f2a8e630fd", "ccl-6f43f9bb8ff2d712", "ccl-08c109442f3e666d"]):
                            description = candidate.get_text(strip=True)
                            break
                    if not description:
                        raise Exception("No description found")
                except:
                    description = title

                try:
                    style = item.select_one("div.ccl-a897ba3fd642670d")
                    image_url = self.ctx.url_formatter(style.get("style")) if style else ""
                except:
                    image_url = ""
                
                image_file_name = image_downloader.get_hashed_filename(title, image_url, self.ctx.category) if image_url else ""

                self.ctx.add_menu_item(MenuItem(title, price, description, image_url, self.ctx.category, image_file_name))
            except Exception as e:
                logging.warning(f"Deliveroo parse error: {e}")

class TalabatParser:
    def __init__(self, ctx):
        self.ctx = ctx

    def parse(self, soup):
        if self.ctx.menu_type == "single":
            categories = soup.select(self.ctx.items_selector["selector"])[1:]
            for order, cat in enumerate(categories, start=1):
                self.ctx.category = cat.select_one("h4").text.strip()
                append_to_excel(
                    os.path.join(self.ctx.report_path, "cat.xlsx"),
                    excel_attributes={"CategoryName": self.ctx.category, "DisplayOrder": order}
                )

                selectors = [
                    "div.sc-a31f9fb2-0.dyJtfK",
                    "div.sc-a31f9fb2-0.eQGrrN"
                ]

                for selector in selectors:
                    self._parse_items(cat.select(selector))
        elif self.ctx.menu_type == "multi":
            items = soup.select(self.ctx.items_selector["selector"])
            self._parse_items(items)
        else:
            logging.warning("Invalid menu type for Talabat")

    def _parse_items(self, items):
        for item in items:
            try:
                title = sanitizer.sanitize_text(item.select_one("div.f-15").text.strip())
                price = item.select_one("span.currency")
                price = price.text.strip() if price else ""
                desc_tag = item.select_one("div.f-12.description")
                description = desc_tag.text.strip() if desc_tag and desc_tag.text.strip() else title
                
                image = item.select_one("img[src^='https://images.deliveryhero.io/image/']")
                image_url = self.ctx.url_formatter(image["src"]) if image else ""
                image_file_name = image_downloader.get_hashed_filename(title, image_url, self.ctx.category) if image_url else ""
                self.ctx.add_menu_item(MenuItem(title, price, description, image_url, self.ctx.category, image_file_name))
            except Exception as e:
                logging.warning(f"Talabat parse error: {e}")

class InstashopParser:
    def __init__(self, ctx):
        self.ctx = ctx

    def parse(self, soup):
        items = soup.select(self.ctx.items_selector["selector"])
        for item in items:
            try:
                title = sanitizer.sanitize_text(item.select_one("div.product-title").text.strip()) or 'test'
                description = item.select_one("div.product-packaging-string").text.strip()
                if description == "":
                    description = title
                price_tag = item.select_one("div.price-txt span:nth-of-type(2)") or item.select_one("div.discount-price")
                price = price_tag.text.strip() if price_tag else ""
                image = item.select_one("img[src^='https://cdn.instashop.ae/']")
                image_url = image["src"] if image else ""
                image_file_name = image_downloader.get_hashed_filename(title, image_url, self.ctx.category) if image_url else ""
                self.ctx.add_menu_item(MenuItem(title, price, description, image_url, self.ctx.category, image_file_name))
            except Exception as e:
                logging.warning(f"Instashop parse error: {e}")
                             
class NoonParser:
    def __init__(self, ctx):
        self.ctx = ctx

    def parse_multiple_htmls(self, html_list):
        for category_dict in html_list[1:]:
            soup = BeautifulSoup(category_dict.get("html", None), "html.parser")
            sub_category = category_dict.get("sub_category", None)
            self.parse(soup, sub_category) 

    def parse(self, soup, sub_category=None):
        sub_category = sub_category if sub_category else self.ctx.category
        items = soup.select(self.ctx.items_selector["selector"])
        for item in items:
            try:
                title_tag = item.select_one("div.css-146c3p1.r-8akbws.r-krxsd3.r-dnmrzs.r-1qsk4np.r-1udbk01.r-a023e6.r-1kfrs79.r-1xemd6l.r-14yzgew.r-2yi16")
                title = sanitizer.sanitize_text(title_tag.text.strip()) if title_tag else ""
                desc_tag = item.select_one("div.css-146c3p1.r-dnmrzs.r-1udh08x.r-1udbk01.r-3s2u2q.r-1iln25a.r-1k9kpms.r-n6v787.r-majxgm.r-1xemd6l.r-1cwl3u0.r-knv0ih")
                description = desc_tag.text.strip() if desc_tag and desc_tag.text.strip() else title
                
                price_tag = item.select_one("span.css-1jxf684.r-1k9kpms.r-a023e6.r-majxgm.r-1rsjblm.r-1xemd6l.r-14yzgew.r-142tt33") or item.select_one("span.css-1jxf684.r-1792100.r-a023e6.r-vw2c0b.r-1rsjblm.r-1xemd6l.r-14yzgew")
                price = price_tag.text.strip() if price_tag else ""
                image_div = item.select_one("div.css-g5y9jx.r-1niwhzg.r-vvn4in.r-u6sd8q.r-1p0dtai.r-1pi2tsx.r-1d2f490.r-u8s1d.r-zchlnj.r-ipm5af.r-13qz1uu.r-1wyyakw.r-x3cy2q")
                image_url = self.ctx.url_formatter(image_div["style"]) if image_div and "style" in image_div.attrs else ""
                image_file_name = image_downloader.get_hashed_filename(title, image_url, sub_category) if image_url else ""
                self.ctx.add_menu_item(MenuItem(title, price, description, image_url, sub_category, image_file_name))
            except Exception as e:
                logging.warning(f"Noon parse error: {e}")

class ItemsDownloader:
    def __init__(self, **kwargs):
        self.menu_url = kwargs.get("menu_url")
        self.menu_type = kwargs.get("menu_type")
        self.category = kwargs.get("category")
        self.parser_name = kwargs.get("parser")
        self.items_selector = kwargs.get("items_list")
        self.url_formatter = kwargs.get("url_formatter")
        self.report_path = kwargs.get("report_path")
        self.download_path = kwargs.get("downloaded_images_path")
        self.is_headless = kwargs.get("is_headless", True)
        self.scroll_type = kwargs.get("scroll_type")
        self.menu = []

    def add_menu_item(self, item: MenuItem):
        if not self._item_exists(item):
            self.menu.append(item.to_dict())

    def _item_exists(self, new_item):
        return any(
            existing["title"] == new_item.title and
            existing["price"] == new_item.price and
            existing.get("description", "") == new_item.description and
            existing["CategoryName"] == new_item.category
            for existing in self.menu
        )

    def _get_parser(self):
        parser_map = {
            "deliveroo": DeliverooParser,
            "talabat": TalabatParser,
            "instashop": InstashopParser,
            "noon": NoonParser,
        }
        parser_cls = parser_map.get(self.parser_name)
        return parser_cls(self) if parser_cls else None

    def fast_scroll_down(self, page, max_retries=5, wait_time=1.5):
        last_height = page.evaluate("() => document.body.scrollHeight")
        retries = 0
        while retries < max_retries:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(wait_time)
            new_height = page.evaluate("() => document.body.scrollHeight")

            if new_height == last_height:
                retries += 1
            else:
                retries = 0

            last_height = new_height

    def slow_scroll_down(self, page):
        page.evaluate("""
            async () => {
                await new Promise(resolve => {
                    let totalHeight = 0;
                    const distance = 100;
                    const delay = 50;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= scrollHeight - window.innerHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, delay);
                });
            }
        """)

    def _handle_noon_page(self, page):
        """Handle Noon-specific page interactions and return html_snapshots."""
        html_snapshots = [page.content()]
        try:
            try:
                page.wait_for_selector("button:has-text('Accept All')", timeout=5000)
                cookie_button = page.query_selector("button:has-text('Accept All')")
                if cookie_button:
                    cookie_button.click()
                    print("✅ Accepted cookies.")
                    page.wait_for_timeout(2000)
                else:
                    print("⚠️ No cookie button found.")
            except Exception as e:
                print(f"⚠️ Error accepting cookies: {e}")

            total_buttons = page.query_selector_all("button.categorysidebar_categoryNavItem__OHXgV ")
            if total_buttons:
                for i in range(1, len(total_buttons)):
                    try:
                        page.wait_for_timeout(1000)
                        buttons = page.query_selector_all("button.categorysidebar_categoryNavItem__OHXgV ")
                        btn = buttons[i]
                        sub_category = btn.query_selector("div.categorysidebar_categoryNavText__on4__").text_content()
                        btn.scroll_into_view_if_needed()
                        btn.click()
                        page.wait_for_timeout(3000)
                        html_snapshots.append({"html": page.content(), "sub_category": sub_category})
                        next_button = page.query_selector('div.Pagination_paginationContainer__Kt4hm li.next a[role="button"]')
                        while next_button:
                            class_attr = next_button.get_attribute("aria-disabled")
                            if "true" in class_attr:
                                next_button = None
                                break
                            next_button.click()
                            next_button = None
                            page.wait_for_timeout(3000)
                            html_snapshots.append({"html": page.content(), "sub_category": sub_category})
                            next_button = page.query_selector('div.Pagination_paginationContainer__Kt4hm li.next a[role="button"]')
                    except Exception as e:
                        logging.warning(f"Failed clicking button #{i}: {e}")
            else:
                try:
                    next_button = page.query_selector('div.Pagination_paginationContainer__Kt4hm li.next a[role="button"]')
                    while next_button:
                        class_attr = next_button.get_attribute("aria-disabled")
                        if "true" in class_attr:
                            next_button = None
                            break
                        next_button.click()
                        next_button = None
                        page.wait_for_timeout(3000)
                        self.slow_scroll_down(page)
                        html_snapshots.append(page.content())
                        next_button = page.query_selector('div.Pagination_paginationContainer__Kt4hm li.next a[role="button"]')
                except Exception as e:
                    logging.warning(f"Failed clicking Next button: {e}")
        except Exception as e:
            logging.warning(f"Error while processing Noon page: {e}")
        return html_snapshots

    def start(self):
        with Stealth().use_sync(sync_playwright()) as playwright:
            USER_AGENTS = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ]
            user_agent = random.choice(USER_AGENTS)
            browser = playwright.chromium.launch(headless=self.is_headless, args=["--no-sandbox", "--start-maximized"])
            context = browser.new_context(no_viewport=True, user_agent=user_agent)
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            retries = 5
            for attempt in range(1, retries + 1):
                try:
                    print(f"[Attempt {attempt}] Trying to load: {self.menu_url}")
                    page.goto(self.menu_url, wait_until="load", timeout=60000)
                    break
                except PlaywrightTimeoutError as e:
                    print(f"[Attempt {attempt}] Timeout loading {self.menu_url}: {e}")
                except Exception as e:
                    print(f"[Attempt {attempt}] Unexpected error: {e}")

                if attempt < retries:
                    print(f"Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f"Failed to load page after {retries} attempts. Skipping.")
                    browser.close()
                    return

            page.wait_for_timeout(5000)

            if self.scroll_type == 'slow':
                self.slow_scroll_down(page)
            else:
                page.wait_for_selector("div.product-title")
                self.fast_scroll_down(page)

            if self.parser_name == "noon":
                html_snapshots = self._handle_noon_page(page)
            else:
                try:
                    html_snapshots = [page.content()]
                except Exception as e:
                    print(f"Failed to get page content: {e}")
                    browser.close()
                    return

            browser.close()

            parser = self._get_parser()
            if not parser:
                logging.error("No valid parser provided.")
                return

            if hasattr(parser, 'parse_multiple_htmls') and len(html_snapshots) > 1:
                if isinstance(html_snapshots[1], dict):
                    parser.parse_multiple_htmls(html_snapshots)
                else:
                    for html in html_snapshots:
                        soup = BeautifulSoup(html, "html.parser")
                        parser.parse(soup)
            else:
                soup = BeautifulSoup(html_snapshots[0], "html.parser")
                parser.parse(soup)

            for item in self.menu:
                try:
                    if item["image"]:
                        image_downloader.download_image(item["Image_File_Name"], item["image"], self.download_path)
                except Exception as e:
                    logging.warning(f"Image download error for {item['title']}: {e}")