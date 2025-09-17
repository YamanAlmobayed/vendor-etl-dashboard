from celery import shared_task
import logging
from celery_singleton import Singleton
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='auto_approve', base=Singleton, raise_on_duplicate=True, unique_on=['task'])
def auto_approve(self, task, username, password):
    """Auto-approve items using multithreaded browser automation."""
    logger.info(f"Auto-approve task started for user: {username}")
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Starting auto-approval process'})
        
        LOGIN_URL = "https://cp.elitewherego.net/login"
        MAIN_URL = "https://cp.elitewherego.net/admin/item/list"
        INDICES = [0, 2, 4, 6, 8]
        
        with ThreadPoolExecutor(max_workers=len(INDICES)) as executor:
            futures = [
                executor.submit(_process_approval_thread, btn_index, LOGIN_URL, MAIN_URL, username, password)
                for btn_index in INDICES
            ]
            
            for future in futures:
                future.result()
        
        logger.info("Auto-approve task completed successfully")
        return {
            'status': 'completed',
            'message': 'All Items have been Approved'
        }
        
    except Exception as e:
        logger.error(f"Auto-approve task failed: {e}")
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise

def _process_approval_thread(btn_index, login_url, main_url, username, password):
    """Process item approval in a separate thread."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto(login_url)
            page.fill("input[name='email']", username)
            page.fill("input[name='password']", password)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            
            page.goto(main_url)
            page.wait_for_selector("a[href^='https://cp.elitewherego.net/admin/item/edit/']")
            
            edit_buttons = page.query_selector_all("a[href^='https://cp.elitewherego.net/admin/item/edit/']")
            
            while btn_index < len(edit_buttons):
                edit_buttons[btn_index].click()
                page.wait_for_load_state("networkidle")
                
                update_button = page.wait_for_selector("button[type='submit']", timeout=5000)
                if update_button:
                    update_button.click()
                    page.wait_for_load_state("networkidle")
                    logger.info(f"Item at index {btn_index} updated successfully")
                
                if btn_index == 0 and len(edit_buttons) == 1:
                    break
                
                page.wait_for_selector("a[href^='https://cp.elitewherego.net/admin/item/edit/']")
                edit_buttons = page.query_selector_all("a[href^='https://cp.elitewherego.net/admin/item/edit/']")
                
        except Exception as e:
            logger.error(f"Error processing approval thread {btn_index}: {e}")
        finally:
            browser.close()