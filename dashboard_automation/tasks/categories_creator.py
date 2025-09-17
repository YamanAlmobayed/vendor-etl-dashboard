import pandas as pd
from playwright.sync_api import sync_playwright
from celery import shared_task
from celery_singleton import Singleton
import logging
from utils.file_cleanup_manager import FileCleanupManager, cleanup_task_files
from utils.helper_methods import perform_vendor_login, update_task_progress

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='create_categories', base=Singleton, raise_on_duplicate=True, unique_on=['task'])
def create_categories(self, task, vendor_email, vendor_password, excel_path):
    """Create categories from Excel data using web automation."""
    logger.info(f"Category creation task started for {vendor_email}")
    task_id = self.request.id
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Initializing task'})
        
        FileCleanupManager.register_files_for_cleanup(task_id, [excel_path])
        
        excel_data = _read_categories_excel(excel_path)
        total_count = len(excel_data)
        logger.info(f"Found {total_count} categories to process")
        
        processed_count = _process_categories_with_browser(
            self, vendor_email, vendor_password, excel_data
        )
        
        logger.info(f"Task completed. Successfully processed {processed_count}/{total_count} categories")
        
        return {
            'status': 'completed',
            'message': f'Category creation completed. Processed {processed_count}/{total_count} categories successfully',
            'processed': processed_count,
            'total': total_count
        }
        
    except Exception as e:
        logger.error(f"Categories creation task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise
    finally:
        cleanup_task_files.apply_async(args=[task_id], countdown=5)

def _read_categories_excel(excel_path):
    """Read and validate categories Excel file."""
    try:
        df = pd.read_excel(
            excel_path, 
            usecols=[0, 1], 
            names=["category", "display_order"], 
            header=0
        )
        return df.to_dict(orient='records')
    except Exception as e:
        raise ValueError(f"Failed to read Excel file {excel_path}: {e}")

def _process_categories_with_browser(self, vendor_email, vendor_password, excel_data):
    """Process categories using browser automation."""
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/categories"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            perform_vendor_login(page, LOGIN_URL, vendor_email, vendor_password)
            page.goto(MAIN_URL)
            
            processed_count = 0
            for index, row in enumerate(excel_data):
                try:
                    logger.info(f"Processing category {index + 1}/{len(excel_data)}: {row['category']}")
                    
                    page.click("a[href='https://vendor.elitewherego.com/categories/create']")
                    page.wait_for_selector('div.max-w-3xl.overflow-hidden.bg-white.rounded.shadow')
                    
                    page.fill("#name", str(row['category']))
                    page.fill("#display_order", str(row['display_order']))
                    page.select_option("#status", "Active")
                    
                    page.click("button[type='submit']")
                    page.wait_for_selector('table.w-full.whitespace-nowrap')
                    
                    processed_count += 1
                    logger.info(f"Category '{row['category']}' created successfully")
                    
                    update_task_progress(self, processed_count, len(excel_data), "categories")
                    
                except Exception as e:
                    logger.error(f"Error processing category '{row['category']}': {e}")
                    page.goto(MAIN_URL)
                    page.wait_for_selector('table.w-full.whitespace-nowrap')
                    continue
            
            return processed_count
            
        finally:
            browser.close()