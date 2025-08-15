from celery import shared_task
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor
import time 


@shared_task(bind=True)
def sample_task(self):
    duration = 10
    for i in range(duration):
        time.sleep(1)
        self.update_state(state='PROGRESS', meta={'current': i+1, 'total': duration})
    return {'result': 'Task completed!'}


@shared_task(bind=True)
def auto_approve(self, username, password):
    self.update_state(state='PROGRESS')
    LOGIN_URL = "https://cp.elitewherego.net/login"
    MAIN_URL = "https://cp.elitewherego.net/admin/item/list"
    INDICES = [0, 2, 4, 6, 8]  # Default button indices
    print(username, password)

    def process_item(btn_index):
        print(btn_index)
#        with sync_playwright() as p:
#            browser = p.chromium.launch(headless=True)
#            context = browser.new_context()
#            page = context.new_page()
#
#            try:
#                # Login once
#                page.goto(LOGIN_URL)
#                page.fill("input[name='email']", username)
#                page.fill("input[name='password']", password)
#                page.click("button[type='submit']")
#                page.wait_for_load_state("networkidle")
#
#                # Navigate to item list
#                page.goto(MAIN_URL)
#                page.wait_for_selector("a[href^='https://cp.elitewherego.net/admin/item/edit/']")
#
#                edit_buttons = page.query_selector_all(
#                    "a[href^='https://cp.elitewherego.net/admin/item/edit/']"
#                )
#                if btn_index >= len(edit_buttons):
#                    print(f"Index {btn_index} out of range, skipping.")
#                    return
#
#                # Click edit for the specific index
#                edit_buttons[btn_index].click()
#                page.wait_for_load_state("networkidle")
#
#                # Update item
#                update_button = page.wait_for_selector("button[type='submit']", timeout=5000)
#                if update_button:
#                    update_button.click()
#                    page.wait_for_load_state("networkidle")
#                    print(f"Item at index {btn_index} updated successfully.")
#
#            except Exception as e:
#                print(f"Error processing item {btn_index}: {e}")
#
#            finally:
#                browser.close()

    # Run all indices in parallel
    with ThreadPoolExecutor(max_workers=len(INDICES)) as executor:
        executor.map(process_item, INDICES)
        
    return {'result': 'Task completed!'}
        
    
