from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth
import openpyxl
from openpyxl.styles import Font
from celery import shared_task
import os
import re
import time
import random
import logging
import traceback
from pathlib import Path
from utils.file_cleanup_manager import FileCleanupManager, cleanup_task_files
from utils.sanitizers import deliveroo_addon_price_cleaner

logger = logging.getLogger(__name__)


class BaseAddonScraper:
    """Base class with shared functionality for all scrapers."""
    
    def __init__(self, url, base_path):
        self.url = url
        self.base_path = base_path
        self.cat_attributes = []
        self.addon_attributes = []
        self.items_addons_attributes = []
        os.makedirs(base_path, exist_ok=True)

    def append_to_excel(self, filename, data):
        """Append data to an Excel file, creating headers if file does not exist."""
        excel_path = os.path.join(self.base_path, filename)
        try:
            workbook = openpyxl.load_workbook(excel_path)
            sheet = workbook.active
        except FileNotFoundError:
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            bold_font = Font(bold=True)
            sheet.append(list(data.keys()))
            for cell in sheet[1]:
                cell.font = bold_font

        sheet.append(list(data.values()))
        workbook.save(excel_path)

    def capitalize_sentence(self, sentence):
        return ' '.join(word.capitalize() for word in sentence.split())

    def dict_exists(self, target_dict, data_list, keys):
        """Check if a dictionary with specific keys exists in a list."""
        return any(all(d.get(key) == target_dict.get(key) for key in keys) for d in data_list)

    def save_to_excel(self):
        """Save scraped data to Excel files."""
        for data, filename in zip(
            [self.cat_attributes, self.addon_attributes, self.items_addons_attributes],
            ["addon_cat.xlsx", "addons.xlsx", "items_addons.xlsx"]
        ):
            for row in data:
                self.append_to_excel(filename, row)

    def start(self):
        """Start the scraping process."""
        self.scrape()
        self.save_to_excel()
        logger.info("Scraping and saving complete!")


class DeliverooAddonScraper(BaseAddonScraper):
    """Scraper for Deliveroo platform."""
    
    def __init__(self, url, base_path, page):
        super().__init__(url, base_path)
        self.page = page

    def extract_addon_details(self, addon):
        """Extract addon name and price from a given addon element."""
        try:
            addon_name = addon.query_selector('p.ccl-649204f2a8e630fd.ccl-a396bc55704a9c8a.ccl-0956b2f88e605eb8.ccl-40ad99f7b47f3781').text_content()
        except:
            addon_name = addon.query_selector('p.ccl-649204f2a8e630fd.ccl-a396bc55704a9c8a.ccl-f88600790342ab98.ccl-40ad99f7b47f3781').text_content()
        try:
            addon_price = deliveroo_addon_price_cleaner(addon.query_selector('div.ccl-a206e125970432e3').text_content())
        except:
            addon_price = ''
        return addon_name, addon_price

    def extract_addon_categories(self, category, selector):
        """Extract all addon categories and their items from the menu."""
        for item in category.query_selector_all(selector):
            try:
                try:
                    item_name = item.query_selector(
                        'p.ccl-649204f2a8e630fd.ccl-a396bc55704a9c8a.ccl-0956b2f88e605eb8.ccl-ff5caa8a6f2b96d0.ccl-40ad99f7b47f3781'
                    ).text_content().strip()
                except:
                    item_name = item.query_selector('p.ccl-649204f2a8e630fd.ccl-98a86d2cf2dd0739.ccl-5009081e3e0d6b1f.ccl-0956b2f88e605eb8.ccl-ff5caa8a6f2b96d0.ccl-40ad99f7b47f3781').text_content().strip()
                
                item.click()
                self.page.wait_for_timeout(2000)

                addon_window = self.page.query_selector('div.ccl-e2683e5cd3d2680f')
                addon_categories = addon_window.query_selector_all("div.MenuItemModifiers-60c359b419ec39f6")
                
                for addon_category in addon_categories:
                    addons = addon_category.query_selector_all('div.ccl-a5e1512b87ef2079')
                    addon_details = []
                    for addon in addons:
                        addon_name, addon_price = self.extract_addon_details(addon)
                        addon_details.append((addon_name, addon_price))
                        
                    is_radio = addon_category.query_selector("span.ccl-8e142e574bc93bc8")
                    is_checkbox = addon_category.query_selector("span.ccl-4854e6e9ca9d5442")
                    is_addbutton = addon_category.query_selector("div.ccl-4704108cacc54616.ccl-8cbd6e52f5095feb")

                    addon_category_name = self.capitalize_sentence(
                        addon_category.query_selector(
                            "p.ccl-649204f2a8e630fd.ccl-a396bc55704a9c8a.ccl-0956b2f88e605eb8.ccl-ff5caa8a6f2b96d0.ccl-40ad99f7b47f3781"
                        ).text_content().strip()
                    )

                    addon_count = ''
                    
                    if is_radio:
                        category_status = "Yes"
                        addon_count = "1"
                        
                    elif is_addbutton:
                        category_status = "No"
                        single_addon_row = addon_category.query_selector("div.ccl-a5e1512b87ef2079")
                        counter = 0
                        while True:
                            is_disabled = single_addon_row.query_selector('[aria-label="Increase quantity"][disabled]')
                            if is_disabled:
                                break
                            addbutton = single_addon_row.query_selector('[aria-label="Increase quantity"]')   
                            addbutton.click()
                            self.page.wait_for_timeout(500)
                            counter = counter + 1

                        addon_count = str(counter)
                        
                    elif is_checkbox:
                        add_to_cart_button = addon_window.query_selector('button.ccl-388f3fb1d79d6a36.ccl-6d2d597727bd7bab.ccl-59eced23a4d9e077.ccl-7be8185d0a980278.ccl-18419a95661cda93')
                        if not add_to_cart_button:
                            category_status = "No"
                        elif addon_category.query_selector("p.ccl-649204f2a8e630fd.ccl-6f43f9bb8ff2d712.ccl-08c109442f3e666d.ccl-40ad99f7b47f3781"):
                            category_status = "Yes"
                        else:
                            category_status = ""
                        counter = 0
                        for addon in addon_category.query_selector_all('div.ccl-a5e1512b87ef2079'):
                            is_disabled = addon.query_selector('[type="button"][disabled]')
                            if is_disabled:
                                break
                            addon.click()
                            self.page.wait_for_timeout(500)
                            counter = counter + 1

                        addon_count = str(counter)
                    
                    else: 
                        raise Exception("Unknown Addons category type")

                    cat_data = {
                        'addon_category': addon_category_name,
                        'category_status': category_status,
                        'addon_count_line': addon_count
                    }
                    if not self.dict_exists(cat_data, self.cat_attributes, cat_data.keys()):
                        self.cat_attributes.append(cat_data)
                    
                    for addon_name, addon_price in addon_details:
                        item_data = {
                            'item_name': item_name,
                            'addon_name': addon_name,
                            'addon_price': addon_price,
                            'category_status': category_status,
                        }
                        if not self.dict_exists(item_data, self.items_addons_attributes, item_data.keys()):
                            self.items_addons_attributes.append(item_data)

                        addon_data = {
                            'addon_category': addon_category_name,
                            'addon_name': addon_name,
                            'addon_price': addon_price,
                            'category_status': category_status
                        }
                        if not self.dict_exists(addon_data, self.addon_attributes, addon_data.keys()):
                            self.addon_attributes.append(addon_data)

                addon_window.query_selector("button.ccl-4704108cacc54616.ccl-4f99b5950ce94015").click()
            except Exception as e:
                logger.warning(f"Error processing item: {e}")

    def scrape(self):
        """Scrape the menu from the given URL."""
        categories = self.page.query_selector_all("div.Layout-4549ebf43c78c99a")[1:]
        for category in categories:
            self.extract_addon_categories(category, "div.MenuItemCard-a927b3314fc88b17")


class TalabatAddonScraper(BaseAddonScraper):
    """Scraper for Talabat platform."""
    
    def extract_addon_details(self, addon):
        """Extract addon name and price from a given addon element."""
        label = addon.query_selector('label.control-label > span:nth-of-type(2)')
        text_span = addon.query_selector('span.text span')

        addon_name = (label or text_span).text_content().split('(')[0].strip() if label or text_span else None
        price_element = addon.query_selector('label[data-testid="radio"] span.currency') or \
                        addon.query_selector('label.control-label span.currency')
        addon_price = price_element.text_content().strip() if price_element else '0'

        return addon_name, addon_price

    def extract_addon_categories(self, page, category, selector):
        """Extract all addon categories and their items from the menu."""
        category_name = category.query_selector('h4.f-20.f-500').inner_html().strip()
        for item in category.query_selector_all(selector):
            try:
                item_name = item.query_selector('div.f-15').text_content().strip()
                item.click()
                try:
                    page.wait_for_selector('div.text-wrap.f-400.f-14.f-500', timeout=5000)
                except:
                    continue

                addon_window = page.query_selector('div.modal-content')

                while True:
                    addon_categories = addon_window.query_selector_all("div.sc-1bf12ad-0.ilBSTs")
                    clicked = False

                    for addon_category in addon_categories:
                        is_radio = addon_category.query_selector("div[data-testid='choices-radio-buttons-component']")
                        addons = addon_category.query_selector_all('div.col-lg-5.col-md-5.col-sm-16.col-16')

                        for addon in addons:
                            if not addon.get_attribute("data-clicked"):
                                addon.click()
                                page.wait_for_timeout(300)
                                addon.evaluate("el => el.setAttribute('data-clicked', 'true')")
                                clicked = True

                                if is_radio:
                                    break
                        if clicked:
                            break
                        
                    if not clicked:
                        break
                
                addon_categories = addon_window.query_selector_all("div.sc-1bf12ad-0.ilBSTs")

                for addon_category in addon_categories:
                    has_checkboxes = addon_category.query_selector("div[data-testid='choices-checkboxes-component']")
                    category_status = "No" if has_checkboxes else "Yes"
                    
                    addon_category_name = self.capitalize_sentence(addon_category.query_selector("strong[data-test='sectionName']").text_content().strip())
                    count_text = addon_category.query_selector("span.dark-gray.align-middle").text_content()
                    addon_count = re.search(r'\d+', count_text).group() if re.search(r'\d+', count_text) else \
                                  len(addon_category.query_selector_all('div.col-lg-5.col-md-5.col-sm-16.col-16'))

                    cat_data = {
                        'addon_category': addon_category_name,
                        'category_status': category_status,
                        'addon_count_line': addon_count
                    }
                    if not self.dict_exists(cat_data, self.cat_attributes, cat_data.keys()):
                        self.cat_attributes.append(cat_data)

                    for addon in addon_category.query_selector_all('div.col-lg-5.col-md-5.col-sm-16.col-16'):
                        addon_name, addon_price = self.extract_addon_details(addon)
                        
                        item_data = {
                            'category_name': category_name,
                            'item_name': item_name,
                            'addon_category': addon_category_name,
                            'addon_name': addon_name,
                            'addon_price': addon_price,
                            'category_status': category_status,
                        }
                        if not self.dict_exists(item_data, self.items_addons_attributes, item_data.keys()):
                            self.items_addons_attributes.append(item_data)

                        addon_data = {
                            'addon_category': addon_category_name,
                            'addon_name': addon_name,
                            'addon_price': addon_price,
                            'category_status': category_status
                        }
                        if not self.dict_exists(addon_data, self.addon_attributes, addon_data.keys()):
                            self.addon_attributes.append(addon_data)

                addon_window.query_selector("span.clickable.close-span").click()
            except Exception as e:
                logger.warning(f"Error processing item: {e}")

    def scrape(self):
        """Scrape the menu from the given URL."""
        USER_AGENTS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ]
        
        with Stealth().use_sync(sync_playwright()) as p:
            user_agent = random.choice(USER_AGENTS)
            browser = p.chromium.launch(headless=True, args=["--start-maximized", "--no-sandbox"])
            context = browser.new_context(no_viewport=True, user_agent=user_agent)        
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            retries = 5
            for attempt in range(1, retries + 1):
                try:
                    logger.info(f"[Attempt {attempt}] Trying to load: {self.url}")
                    page.goto(self.url, wait_until="load", timeout=30000)
                    break
                except PlaywrightTimeoutError as e:
                    logger.warning(f"[Attempt {attempt}] Timeout loading {self.url}: {e}")
                except Exception as e:
                    logger.warning(f"[Attempt {attempt}] Unexpected error: {e}")

                if attempt < retries:
                    logger.info(f"Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    logger.error(f"Failed to load page after {retries} attempts. Skipping.")
                    browser.close()
                    return

            page.wait_for_timeout(5000)
            
            categories = page.query_selector_all("div[data-testid='menu-category']")[1:]

            for category in categories:
                self.extract_addon_categories(page, category, "div.sc-a31f9fb2-0.dyJtfK.d-flex.justify-content-between.py-2.clickable")
                self.extract_addon_categories(page, category, "div.sc-a31f9fb2-0.eQGrrN.d-flex.justify-content-between.py-2.clickable")

            browser.close()


def detect_platform_from_url(url):
    """Detect platform type from URL."""
    url_lower = url.lower()
    if 'deliveroo' in url_lower:
        return 'deliveroo'
    elif 'talabat' in url_lower:
        return 'talabat'
    return None


def create_vendor_zip(scrapped_dir, vendor_name):
    """Create zip file of vendor data."""
    import shutil
    try:
        zip_path = os.path.join(scrapped_dir, vendor_name)
        shutil.make_archive(zip_path, 'zip', zip_path)
        shutil.rmtree(zip_path)
        return {
            'status': 'completed',
            'message': f'{vendor_name} addons have been downloaded successfully',
            'download_link': f'{os.getenv("BASE_URL")}{zip_path}.zip'
        }
    except Exception as e:
        logger.error(f"Error creating zip file: {e}")
        raise


@shared_task(bind=True, name="addons_scraper")
def scrape_menu_addons(self, menu_url, vendor_name):
    """Celery task to scrape menu addons from Deliveroo or Talabat."""
    task_id = self.request.id
    
    try:
        # Detect platform
        platform = detect_platform_from_url(menu_url)
        if not platform:
            raise ValueError(f"Unable to determine platform from URL: {menu_url}")
        
        # Setup paths
        base_dir = Path('scrapped_data')
        vendor_dir = os.path.join(base_dir, vendor_name)
        addons_path = os.path.join(vendor_dir, "addons")
        
        os.makedirs(addons_path, exist_ok=True)
        
        # Register for cleanup
        FileCleanupManager.register_files_for_cleanup(task_id, [vendor_dir])
        
        self.update_state(
            state='PROGRESS',
            meta={
                'message': f'Starting to scrape {platform} addons for {vendor_name}',
                'vendor_name': vendor_name,
                'platform': platform
            }
        )
        
        # Scrape based on platform
        if platform == 'deliveroo':
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--start-maximized"])
                context = browser.new_context(
                    no_viewport=True,
                    geolocation={"latitude": 25.186054760669197, "longitude": 55.27504936531868},
                    permissions=["geolocation"]
                )

                page = context.new_page()
                page.goto(menu_url)
                page.wait_for_timeout(20000)

                self.update_state(
                    state='PROGRESS',
                    meta={'message': f'Scraping Deliveroo addons from {menu_url}'}
                )

                scraper = DeliverooAddonScraper(url=menu_url, base_path=addons_path, page=page)
                scraper.start()
                browser.close()
                
        elif platform == 'talabat':
            self.update_state(
                state='PROGRESS',
                meta={'message': f'Scraping Talabat addons from {menu_url}'}
            )
            
            scraper = TalabatAddonScraper(url=menu_url, base_path=addons_path)
            scraper.start()
        
        self.update_state(
            state='PROGRESS',
            meta={'message': f'Creating zip file for {vendor_name}'}
        )
        
        zip_result = create_vendor_zip(base_dir, vendor_name)
        
        # Schedule cleanup
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
        logger.error(f"Error in addons scraper: {e}\n{traceback.format_exc()}")
        raise