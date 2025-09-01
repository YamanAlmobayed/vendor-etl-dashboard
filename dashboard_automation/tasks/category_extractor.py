import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from celery import shared_task
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='scrape_categories')
def scrape_categories(self, vendor_name, vendor_email, vendor_password,):
    logger.info("Category scraping task started")
    logger.info(self)
    logger.info(f"Processing with vendor email: {vendor_email}")
    self.update_state(state='PROGRESS')
    
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/categories"
        
    scrapped_dir = Path('scrapped_data')
     
    reports_path = scrapped_dir / vendor_name / 'reports' 
    reports_path.mkdir(parents=True, exist_ok=True)
    excel_file_path = reports_path / "cat_with_id.xlsx"
    
    def get_html(page, cat_url):
        """Get HTML content from a category URL"""
        page.goto(cat_url)
        page.wait_for_selector('table.w-full.whitespace-nowrap')
        html = page.content()
        return BeautifulSoup(html, "html.parser")
    
    def extract_categories_from_page(soup):
        """Extract category data from a single page"""
        categories_data = []
        rows = soup.select('tr')[1:]  # Skip header
        for row in rows:
            try:
                cat_id = row.select('td.border-t')[0].text.strip()
                cat_name = row.select('td.border-t')[1].text.strip()
                categories_data.append({'CategoryID': cat_id, 'CategoryName': cat_name})
            except IndexError:
                logger.warning("Skipping row due to unexpected structure")
                continue
        return categories_data
    
    try:
        rows_dict_list = []
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            try:
                # Login
                logger.info("Attempting login...")
                page.goto(LOGIN_URL)
                page.fill("#email", vendor_email)
                page.fill("input[type='password']", vendor_password)
                page.click("button[type='submit']")
                page.wait_for_url('https://vendor.elitewherego.com/')
                logger.info("Login successful")
                
                # Navigate to categories page
                page.goto(MAIN_URL)
                page.wait_for_selector('table.w-full.whitespace-nowrap')
                
                # Get initial page content
                soup = BeautifulSoup(page.content(), "html.parser")
                
                # Check for pagination
                categories_links = soup.select("a[href*='?page=']")
                categories_links = categories_links[:-1] if categories_links else []  # Skip the last "Next »" button
                
                if not categories_links:
                    # No pagination - single page
                    logger.info("No pagination found, processing single page")
                    categories_data = extract_categories_from_page(soup)
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
                    # Multiple pages - process each page
                    total_pages = len(categories_links)
                    logger.info(f"Found {total_pages} pages to process")
                    
                    # Process first page (current page)
                    categories_data = extract_categories_from_page(soup)
                    rows_dict_list.extend(categories_data)
                    logger.info(f"Processed page 1/{total_pages + 1}, found {len(categories_data)} categories")
                    
                    # Update progress for first page
                    progress = int((1 / (total_pages + 1)) * 100)
                    self.update_state(
                        state='PROGRESS',
                        meta={
                            'current': 1,
                            'total': total_pages + 1,
                            'progress': progress,
                            'status': f"Processed page 1/{total_pages + 1}"
                        }
                    )
                    
                    # Process remaining pages
                    for index, cat_link in enumerate(categories_links, 2):
                        try:
                            cat_url = cat_link.get("href")
                            logger.info(f"Processing page {index}/{total_pages + 1}: {cat_url}")
                            
                            soup = get_html(page, cat_url)
                            categories_data = extract_categories_from_page(soup)
                            rows_dict_list.extend(categories_data)
                            
                            logger.info(f"Processed page {index}/{total_pages + 1}, found {len(categories_data)} categories")
                            
                            # Update progress
                            progress = int((index / (total_pages + 1)) * 100)
                            self.update_state(
                                state='PROGRESS',
                                meta={
                                    'current': index,
                                    'total': total_pages + 1,
                                    'progress': progress,
                                    'status': f"Processed page {index}/{total_pages + 1}"
                                }
                            )
                            
                        except Exception as e:
                            logger.error(f"Error processing page {index}: {e}")
                            continue
                
                # Save to Excel
                df = pd.DataFrame(rows_dict_list)
                df.to_excel(excel_file_path, index=False)
                logger.info(f"Excel saved to: {excel_file_path}")
                logger.info(f"Total categories scraped: {len(rows_dict_list)}")
                
            except Exception as e:
                logger.error(f"Error during scraping: {e}")
                raise
            finally:
                browser.close()
                
    except Exception as e:
        logger.error(f"Task failed: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        raise
    
    return {
        'result': f'Category scraping completed. Scraped {len(rows_dict_list)} categories successfully',
        'total_scraped': len(rows_dict_list),
        'download_link': f'http://10.39.1.71/{excel_file_path}'
    }