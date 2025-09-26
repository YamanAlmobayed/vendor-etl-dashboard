from celery import shared_task
import os
import time
import traceback
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from utils.excel_handlers import append_to_excel
from pathlib import Path

class MenuToExcel:
    def __init__(self, **kwargs):
        self.menu_url = kwargs.get('menu_url')
        self.parser = kwargs.get('parser')
        self.report_path = kwargs.get('report_path')
        self.is_headless = kwargs.get('is_headless', True)
        self.categories_list = kwargs.get('categories_list')

    def detect_parser_from_url(self):
        """Automatically detect parser type from URL"""
        parsed_url = urlparse(self.menu_url)
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

    def get_categories_selector(self, parser_type):
        """Get appropriate selector based on parser type"""
        selectors = {
            'deliveroo': "a.UICard-fc546e6554c9acdb.undefined.UICard-469f33f83128feb3.UICard-20e6f74249484648",
            'talabat': "div[data-testid='category-item-component']",
            'instashop': "div.category-item.ng-star-inserted",
            'noon': ""
        }
        return selectors.get(parser_type, "")

    def scroll_down(self, page):
        last_height = page.evaluate("document.body.scrollHeight")
        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(3)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

    def get_parser(self):
        return {
            "deliveroo": self.deliveroo_parser,
            "instashop": self.instashop_parser,
            "noon": self.noon_parser,
            "talabat": self.talabat_parser
        }.get(self.parser, self.no_parser)

    def no_parser(self, *args, **kwargs):
        print("No parser matches the menu provider")
        return None

    def categories_driver(self):
        if not self.parser:
            self.parser = self.detect_parser_from_url()
            
        if not self.parser:
            raise ValueError(f"Unable to determine parser for URL: {self.menu_url}")
        
        if not self.categories_list:
            selector = self.get_categories_selector(self.parser)
            if not selector:
                raise ValueError(f"No selector available for parser: {self.parser}")
            self.categories_list = {'selector': selector}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.is_headless, args=["--start-maximized"])
            context = browser.new_context(
                no_viewport=True,
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(self.menu_url, timeout=60000)
            self.scroll_down(page)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        categories = soup.select(self.categories_list['selector'])
        print(f"Found {len(categories)} categories")
        
        parser = self.get_parser()
        if parser:
            parser(categories)

    def deliveroo_parser(self, categories):
        for category in categories:
            try:
                category_name = category.select_one("span.ccl-649204f2a8e630fd.ccl-98a86d2cf2dd0739 span").text.strip()
                category_url = 'https://deliveroo.ae' + category.get("href", "")
                excel_attributes = {'Category': category_name, 'url': category_url}
                append_to_excel(self.report_path, excel_attributes=excel_attributes)
            except Exception as e:
                print(f"Error processing category: {e}")
                traceback.print_exc()

    def instashop_parser(self, categories):
        for category in categories:
            try:
                category_name = category.select_one("div.category-text.mt-1").text.strip()
                category_url = category.select_one("a.text-decoration-none.ng-star-inserted")["href"]
                excel_attributes = {'Category': category_name, 'url': f'https://instashop.com{category_url}'}
                append_to_excel(self.report_path, excel_attributes=excel_attributes)
            except Exception as e:
                print(f"Error processing category: {e}")
                traceback.print_exc()

    def talabat_parser(self, categories):
        categories_dict_list = []

        for category in categories:
            try:
                category_name = category.select_one("span[data-testid='category-name']").text
                sub_categories = [f"https://www.talabat.com{a['href']}" for a in category.select("a[data-testid='subCategory-a']")]
                categories_dict_list.append({'category_name': category_name, 'sub_categories': sub_categories})
            except Exception as e:
                print(f"Error parsing category: {e}")
                traceback.print_exc()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.is_headless, args=["--start-maximized"])
            context = browser.new_context(
                no_viewport=True,
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            for category_dict in categories_dict_list:
                category_name = category_dict['category_name']
                for sub_category in category_dict['sub_categories']:
                    try:
                        print(f'Sub-category: {sub_category}')
                        page.goto(sub_category)
                        time.sleep(3)

                        append_to_excel(self.report_path, excel_attributes={
                            'Category': category_name,
                            'url': sub_category
                        })

                        soup = BeautifulSoup(page.content(), 'html.parser')
                        sub_pages = soup.select("a.paginate-a[href*='&page=']")

                        for sp in sub_pages:
                            page_num = sp['href'].split('&')[1]
                            paginated_url = f"{sub_category}&{page_num}"
                            print(f"Paginated URL: {paginated_url}")
                            append_to_excel(self.report_path, excel_attributes={
                                'Category': category_name,
                                'url': paginated_url
                            })
                    except Exception as e:
                        print(f"Error processing sub-category: {e}")
                        traceback.print_exc()

            browser.close()

    def noon_parser(self, categories):
        print("Noon parser not implemented yet.")


@shared_task(bind=True, name="scrape_menu_categories")
def scrape_menu_categories(self, menu_url, vendor_name):
    """Celery task to scrape menu categories from various providers."""
    try:
        scrapped_dir = Path('scrapped_data')
        reports_path = scrapped_dir / vendor_name / 'reports'
        reports_path.mkdir(parents=True, exist_ok=True)
        excel_file_path = reports_path / "categories_urls.xlsx"
        
        scraper = MenuToExcel(
            menu_url=menu_url,
            report_path=excel_file_path,
            is_headless=True
        )
        
        self.update_state(
            state='PROGRESS',
            meta={'message': f'Starting to scrape categories from {menu_url}'}
        )
        
        scraper.categories_driver()
        
        return {
            'status': 'completed',
            'message': 'Categories scraping completed successfully',
            'download_link': f'{os.getenv("BASE_URL")}{excel_file_path}'
        }
        
    except Exception as e:
        self.update_state(
            state='FAILURE',
            meta={
                'error': str(e),
                'traceback': traceback.format_exc()
            }
        )
        raise