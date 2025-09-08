import pandas as pd
from playwright.sync_api import sync_playwright
import time
from celery import shared_task
from celery_singleton import Singleton
import logging
from utils.file_cleanup_manager import FileCleanupManager
from utils.file_cleanup_manager import cleanup_task_files
from utils.sanitizers import normalize_text, sanitize_text, format_number
from utils.helper_methods import perform_vendor_login, update_task_progress

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='link_items_addons', base=Singleton, raise_on_duplicate=True, unique_on=['task'])
def link_items_addons(self, task, vendor_email, vendor_password, excel_path):
    """Link items with addons based on Excel data using web automation."""
    logger.info(f"Items addons linking task started for {vendor_email}")
    task_id = self.request.id
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Initializing task'})
        
        FileCleanupManager.register_files_for_cleanup(task_id, [excel_path])
        
        # Extract items data from Excel
        items_data = _extract_items_with_addons(excel_path)
        total_items = len(items_data)
        logger.info(f"Found {total_items} items to process for addon linking")
        
        processed_count = _process_items_addon_linking_with_browser(
            self, vendor_email, vendor_password, items_data
        )
        
        logger.info(f"Task completed. Successfully processed {processed_count}/{total_items} items")
        
        return {
            'result': f'Items addons linking completed. Processed {processed_count}/{total_items} items successfully',
            'processed': processed_count,
            'total': total_items
        }
        
    except Exception as e:
        logger.error(f"Items addon linking task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise
    finally:
        cleanup_task_files.apply_async(args=[task_id], countdown=5)

def _extract_items_with_addons(excel_path):
    """Extract items with their associated addons from Excel."""
    
    try:
        df = pd.read_excel(
            excel_path, 
            usecols=[0, 1, 2, 3, 4, 5], 
            names=['category_name', 'item_name', 'addon_category', 'addon_name', 'addon_price', 'category_status']
        )
        
        result = {}
        for _, row in df.iterrows():
            item = row['item_name']
            addon_details = [
                row['category_name'],
                row['addon_category'],
                row['addon_name'],
                format_number(row['addon_price']),
                row['category_status']
            ]
            result.setdefault(item, []).append(addon_details)
        
        return result
    except Exception as e:
        raise ValueError(f"Failed to extract items data from {excel_path}: {e}")

def _process_items_addon_linking_with_browser(self, vendor_email, vendor_password, items_data):
    """Process item addon linking using browser automation."""
    
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/items"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Login
            perform_vendor_login(page, LOGIN_URL, vendor_email, vendor_password)
            page.goto(MAIN_URL)
            
            processed_count = 0
            total_items = len(items_data)
            
            for item_index, (item_name, attributes) in enumerate(items_data.items()):
                try:
                    logger.info(f"Processing item {item_index + 1}/{total_items}: {item_name}")
                    
                    sanitized_item = sanitize_text(item_name)
                    page.fill("#search", sanitized_item)
                    _wait_for_loading(page)

                    table = page.query_selector("table.w-full.whitespace-nowrap")
                    if not table:
                        logger.warning(f"No table found for item: {item_name}")
                        continue
                    
                    item_links = _extract_item_links(table)
                    item_found = False
                    
                    for item_link in item_links:
                        if _is_matching_item(item_link, item_name, attributes[0][0]):
                            logger.info(f"Found matching item, processing addons for: {item_name}")
                            _process_addons_for_item(page, item_link["link"], attributes)
                            item_found = True
                            break
                    
                    if not item_found:
                        logger.warning(f"No matching item found for: {item_name}")

                    page.goto(MAIN_URL)
                    processed_count += 1
                    
                    # Update progress
                    
                    
                    update_task_progress(self, processed_count, total_items, "items")

                except Exception as e:
                    logger.error(f"Error processing item '{item_name}': {e}")
                    page.goto(MAIN_URL)
                    page.wait_for_selector('table.w-full.whitespace-nowrap')
                    continue
            
            return processed_count
            
        finally:
            browser.close()

def _wait_for_loading(page, timeout=3000):
    """Wait for page loading to complete."""
    try:
        page.wait_for_function("document.documentElement.classList.contains('nprogress-busy')", timeout=timeout)
        page.wait_for_function("!document.documentElement.classList.contains('nprogress-busy')", timeout=timeout)
    except:
        time.sleep(2)

def _extract_item_links(table):
    """Extract item links from table."""
    try:
        edit_buttons = table.query_selector_all("a[href^='https://vendor.elitewherego.com/items/']")
        rows = table.query_selector_all("tr.hover\\:bg-gray-100.focus-within\\:bg-gray-100")
        
        return [
            {
                "link": btn.get_attribute('href'),
                "category": rows[i].query_selector("td:nth-child(1)").text_content().strip(),
                "title": rows[i].query_selector("td:nth-child(2)").text_content().strip(),
                "price": rows[i].query_selector("td:nth-child(3)").text_content().strip(),
            }
            for i, btn in enumerate(edit_buttons)
        ]
    except Exception as e:
        logger.error(f"Error extracting item links: {e}")
        return []

def _is_matching_item(item_link, item_name, expected_category):
    
    return (
        sanitize_text(normalize_text(item_link['title'])) == sanitize_text(normalize_text(item_name)) and
        sanitize_text(normalize_text(item_link['category'])) == sanitize_text(normalize_text(expected_category))
    )

def _process_addons_for_item(page, item_url, addon_attributes):
    """Process addons for a specific item."""
    
    try:
        page.goto(item_url)
        page.wait_for_selector('tr.hover\\:bg-gray-100')
        page.wait_for_timeout(2000)

        addon_rows = page.query_selector_all('tr.hover\\:bg-gray-100')
        addon_data = []

        for row in addon_rows:
            try:
                data = {
                    "row": row,
                    "category": sanitize_text(row.query_selector("td:nth-child(2)").text_content().strip()),
                    "status": row.query_selector("td:nth-child(3)").text_content().strip(),
                    "name": sanitize_text(row.query_selector("td:nth-child(4)").text_content().strip()),
                    "price": row.query_selector("td:nth-child(5)").text_content().strip(),
                    "link": row.query_selector('a[href^="https://vendor.elitewherego.com/addaddon/"]').get_attribute("href")
                }
                addon_data.append(data)
            except Exception as e:
                logger.error(f"Error parsing addon row: {e}")

        for addon_attr in addon_attributes:
            for addon in addon_data:
                if (
                    normalize_text(addon["category"]) == normalize_text(sanitize_text(addon_attr[1])) and
                    normalize_text(addon["name"]) == normalize_text(sanitize_text(addon_attr[2])) and
                    addon["price"] == str(addon_attr[3]) and
                    addon["status"] == addon_attr[4]
                ):
                    try:
                        logger.info(f"Visiting matched addon: {addon['link']}")
                        page.goto(addon["link"])
                        page.wait_for_selector('tr.hover\\:bg-gray-100')
                        page.wait_for_timeout(1000)
                        break
                    except Exception as e:
                        logger.error(f"Error visiting addon: {e}")
                        break
    except Exception as e:
        logger.error(f"Error processing addons for item: {e}")