import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright
from celery import shared_task
from celery_singleton import Singleton
import logging
from utils.file_cleanup_manager import FileCleanupManager
from utils.file_cleanup_manager import cleanup_task_files

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='create_categories', base=Singleton, raise_on_duplicate=True, unique_on=['task',])
def create_categories(self, task, vendor_email, vendor_password, excel_path):
    logger.info("Category creation task started")
    logger.info(self)
    logger.info(f"Processing with vendor email: {vendor_email}")
    self.update_state(state='PROGRESS')
    task_id = self.request.id
    FileCleanupManager.register_files_for_cleanup(task_id, [
                excel_path
             ])

    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/categories"
    
    def read_excel_to_dict_list(file_path: Path) -> list:
        df = pd.read_excel(file_path, usecols=[0, 1], names=["category", "display_order"], skiprows=0, header=[0])
        return df.to_dict(orient='records')
    

    
    try:
        # Read Excel data
        excel_data = read_excel_to_dict_list(excel_path)
        logger.info(f"Found {len(excel_data)} categories to process")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            try:
                # Login
                page.goto(LOGIN_URL)
                page.fill("#email", vendor_email)
                page.fill("input[type='password']", vendor_password)
                page.click("button[type='submit']")
                page.wait_for_url('https://vendor.elitewherego.com/')
                logger.info("Login successful")
                
                # Navigate to categories page
                page.goto(MAIN_URL)
                
                processed_count = 0
                for index, row in enumerate(excel_data):
                    try:
                        logger.info(f"Processing category {index + 1}/{len(excel_data)}: {row['category']}")
                        
                        # Click create category link
                        page.click("a[href='https://vendor.elitewherego.com/categories/create']")
                        page.wait_for_selector('div.max-w-3xl.overflow-hidden.bg-white.rounded.shadow')
                        
                        # Fill form
                        page.fill("#name", row['category'])
                        page.fill("#display_order", str(row['display_order']))
                        page.select_option("#status", "Active")
                        
                        # Submit form
                        page.click("button[type='submit']")
                        page.wait_for_selector('table.w-full.whitespace-nowrap')
                        
                        processed_count += 1
                        logger.info(f"Category '{row['category']}' created successfully")
                        
                        # Update task progress
                        progress = int((processed_count / len(excel_data)) * 100)
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'current': processed_count,
                                'total': len(excel_data),
                                'progress': progress,
                                'status': f"Processed {processed_count}/{len(excel_data)} categories"
                            }
                        )
                        
                    except Exception as e:
                        logger.error(f"Error processing category '{row['category']}': {e}")
                        # Navigate back to categories page and continue
                        page.goto(MAIN_URL)
                        page.wait_for_selector('table.w-full.whitespace-nowrap')
                        continue
                
                logger.info(f"Task completed. Successfully processed {processed_count}/{len(excel_data)} categories")
                
            except Exception as e:
                logger.error(f"Error during automation: {e}")
                raise
            finally:
                cleanup_task_files.apply_async(args=[task_id], countdown=5)
                browser.close()
                
    except Exception as e:
        logger.error(f"Task failed: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        cleanup_task_files.apply_async(args=[task_id], countdown=5)
        raise
    
    return {
        'result': f'Category creation completed. Processed {processed_count}/{len(excel_data)} categories successfully',
        'processed': processed_count,
        'total': len(excel_data)
    }