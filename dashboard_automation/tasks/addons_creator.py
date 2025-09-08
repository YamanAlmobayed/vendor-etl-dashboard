import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright
from celery import shared_task
from celery_singleton import Singleton
import logging
from utils.file_cleanup_manager import FileCleanupManager
from utils.file_cleanup_manager import cleanup_task_files
from utils.helper_methods import perform_vendor_login, update_task_progress

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='create_addons', base=Singleton, raise_on_duplicate=True, unique_on=['task'])
def create_addons(self, task, vendor_email, vendor_password, excel_path):
    """Create addons from Excel data using web automation."""
    logger.info(f"Addon creation task started for {vendor_email}")
    task_id = self.request.id
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Initializing task'})
        
        FileCleanupManager.register_files_for_cleanup(task_id, [excel_path])
        
        # Read Excel data
        excel_data = _read_addons_excel(excel_path)
        total_count = len(excel_data)
        logger.info(f"Found {total_count} addons to process")
        
        processed_count = _process_addons_with_browser(
            self, vendor_email, vendor_password, excel_data
        )
        
        logger.info(f"Task completed. Successfully processed {processed_count}/{total_count} addons")
        
        return {
            'result': f'Addon creation completed. Processed {processed_count}/{total_count} addons successfully',
            'processed': processed_count,
            'total': total_count
        }
        
    except Exception as e:
        logger.error(f"Addons creation task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise
    finally:
        cleanup_task_files.apply_async(args=[task_id], countdown=5)

def _read_addons_excel(excel_path):
    """Read and validate addons Excel file."""
    try:
        df = pd.read_excel(
            excel_path, 
            usecols=[0, 1, 2], 
            names=["addon_category", "addon_name", "addon_price"], 
            header=0
        )
        return df.to_dict(orient='records')
    except Exception as e:
        raise ValueError(f"Failed to read Excel file {excel_path}: {e}")

def _process_addons_with_browser(self, vendor_email, vendor_password, excel_data):
    """Process addons using browser automation."""
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/addons"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Login
            perform_vendor_login(page, LOGIN_URL, vendor_email, vendor_password)
            page.goto(MAIN_URL)
            
            processed_count = 0
            for index, row in enumerate(excel_data):
                try:
                    logger.info(f"Processing addon {index + 1}/{len(excel_data)}: {row['addon_name']}")
                    
                    # Navigate to create form
                    page.click("a[href='https://vendor.elitewherego.com/addons/create']")
                    page.wait_for_selector('#name', timeout=10000)
                    
                    # Fill form
                    page.fill("#name", str(row["addon_name"]))
                    page.fill("#price", str(row["addon_price"]))
                    page.select_option("#addon_category_id", str(row["addon_category"]))
                    page.select_option("#status", "Active")
                    
                    # Submit form
                    page.click("button[type='submit']")
                    page.wait_for_selector('table.w-full.whitespace-nowrap', timeout=10000)
                    
                    processed_count += 1
                    logger.info(f"Addon '{row['addon_name']}' created successfully")
                    
                    # Update progress
                    update_task_progress(self, processed_count, len(excel_data), "addons")
                    
                except Exception as e:
                    logger.error(f"Error processing addon '{row['addon_name']}': {e}")
                    page.goto(MAIN_URL)
                    page.wait_for_selector('table.w-full.whitespace-nowrap')
                    continue
            
            return processed_count
            
        finally:
            browser.close()