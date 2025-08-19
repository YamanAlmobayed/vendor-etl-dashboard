from celery import shared_task
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True)
def auto_approve(self, username, password):
    logger.info("Task started")
    self.update_state(state='PROGRESS')
    LOGIN_URL = "https://cp.elitewherego.net/login"
    MAIN_URL = "https://cp.elitewherego.net/admin/item/list"
    INDICES = [0, 2, 4, 6, 8]  # Default button indices
    logger.info(f"Processing with user: {username} {password}")

    def process_item(btn_index):
        logger.info(btn_index)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Login once
                page.goto(LOGIN_URL)
                page.fill("input[name='email']", username)
                page.fill("input[name='password']", password)
                page.click("button[type='submit']")
                page.wait_for_load_state("networkidle")

                # Navigate to item list
                page.goto(MAIN_URL)
                page.wait_for_selector("a[href^='https://cp.elitewherego.net/admin/item/edit/']")

                edit_buttons = page.query_selector_all(
                    "a[href^='https://cp.elitewherego.net/admin/item/edit/']"
                )
                
                while btn_index <= len(edit_buttons):

                    # Click edit for the specific index
                    edit_buttons[btn_index].click()
                    page.wait_for_load_state("networkidle")

                    # Update item
                    update_button = page.wait_for_selector("button[type='submit']", timeout=5000)
                    if update_button:
                        update_button.click()
                        page.wait_for_load_state("networkidle")
                        print(f"Item at index {btn_index} updated successfully.")
                    if(btn_index == 0 and len(edit_buttons) == 1):
                        break
                    page.wait_for_selector("a[href^='https://cp.elitewherego.net/admin/item/edit/']")    
                    edit_buttons = page.query_selector_all("a[href^='https://cp.elitewherego.net/admin/item/edit/']")

            except Exception as e:
                print(f"Error processing item {btn_index}: {e}")

            finally:
                browser.close()

    # Run all indices in parallel
    with ThreadPoolExecutor(max_workers=len(INDICES)) as executor:
        executor.map(process_item, INDICES)
        
    return {'result': 'Task completed!'}
        
    
