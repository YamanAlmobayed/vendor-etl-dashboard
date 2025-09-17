import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from celery import shared_task
import logging
from utils.helper_methods import perform_vendor_login

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='scrape_categories')
def scrape_categories(self, vendor_name, vendor_email, vendor_password):
    """Scrape categories from vendor website."""
    logger.info(f"Category scraping task started for {vendor_email}")
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Initializing scraping task'})
        
        scrapped_dir = Path('scrapped_data')
        reports_path = scrapped_dir / vendor_name / 'reports'
        reports_path.mkdir(parents=True, exist_ok=True)
        excel_file_path = reports_path / "cat_with_id.xlsx"
        
        rows_dict_list = _scrape_categories_with_browser(self, vendor_email, vendor_password)
        
        df = pd.DataFrame(rows_dict_list)
        df.to_excel(excel_file_path, index=False)
        logger.info(f"Excel saved to: {excel_file_path}")
        logger.info(f"Total categories scraped: {len(rows_dict_list)}")
        
        return {
            'status': 'completed',
            'message': f'Category scraping completed. Scraped {len(rows_dict_list)} categories successfully',
            'total_scraped': len(rows_dict_list),
            'download_link': f'http://10.39.1.71/{excel_file_path}'
        }
        
    except Exception as e:
        logger.error(f"Category scraping task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise

def _scrape_categories_with_browser(self, vendor_email, vendor_password):
    """Perform category scraping using browser automation."""
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/categories"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            perform_vendor_login(page, LOGIN_URL, vendor_email, vendor_password)
            page.goto(MAIN_URL)
            page.wait_for_selector('table.w-full.whitespace-nowrap')
            
            soup = BeautifulSoup(page.content(), "html.parser")
            rows_dict_list = []
            
            categories_links = soup.select("a[href*='?page=']")
            categories_links = categories_links[:-1] if categories_links else []
            
            if not categories_links:
                logger.info("No pagination found, processing single page")
                categories_data = _extract_categories_from_page(soup)
                rows_dict_list.extend(categories_data)
                
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': len(categories_data),
                        'total': len(categories_data),
                        'progress': 100,
                        'status': f"Scraped {len(categories_data)} categories from single page"
                    }
                )
            else:
                total_pages = len(categories_links)
                logger.info(f"Found {total_pages} pages to process")

                for index, cat_link in enumerate(categories_links, 1):
                    try:
                        cat_url = cat_link.get("href")
                        logger.info(f"Processing page {index}/{total_pages}: {cat_url}")

                        soup = _get_html_content(page, cat_url)
                        categories_data = _extract_categories_from_page(soup)
                        rows_dict_list.extend(categories_data)

                        logger.info(f"Processed page {index}/{total_pages}, found {len(categories_data)} categories")

                        progress = int((index / total_pages) * 100)
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'current': index,
                                'total': total_pages,
                                'progress': progress,
                                'status': f"Processed page {index}/{total_pages}"
                            }
                        )

                    except Exception as e:
                        logger.error(f"Error processing page {index}: {e}")
                        continue
            
            return rows_dict_list
            
        finally:
            browser.close()

def _get_html_content(page, cat_url):
    """Get HTML content from a category URL."""
    page.goto(cat_url)
    page.wait_for_selector('table.w-full.whitespace-nowrap')
    html = page.content()
    return BeautifulSoup(html, "html.parser")

def _extract_categories_from_page(soup):
    """Extract category data from a single page."""
    categories_data = []
    rows = soup.select('tr')[1:]
    for row in rows:
        try:
            cat_id = row.select('td.border-t')[0].text.strip()
            cat_name = row.select('td.border-t')[1].text.strip()
            categories_data.append({'CategoryID': cat_id, 'CategoryName': cat_name})
        except IndexError:
            logger.warning("Skipping row due to unexpected structure")
            continue
    return categories_data