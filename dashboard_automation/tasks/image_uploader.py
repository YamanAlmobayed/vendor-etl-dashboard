from celery import shared_task, group
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
import pandas as pd
import glob
from utils import images_counter
from utils import split_images

LOGIN_URL = "https://vendor.elitewherego.com/login"
MAIN_URL = "https://vendor.elitewherego.com/items"
VENDOR_NAME = "Fish And Fire Resturant Llc"

# This is a good place to define a base path that is accessible to all workers
# On a Docker setup, this would be a mounted volume.
# E.g., "/app/data"
BASE_PATH = "/path/to/shared/data" # ⚠️ Update this to your shared volume path

def normalize_text(text):
    return "".join(text.lower().split())

def clean_price(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

# The main function is now a Celery task.
# Use `bind=True` to allow access to task-specific attributes like `self.request`.
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_vendor_volume(self, email, password, folder_path, volume):
    """
    Celery task to process a single volume of images and update the vendor site.
    """
    print(f"[{volume}] Starting processing...")
    
    # Paths must be absolute and accessible to the worker.
    excel_path = os.path.join(BASE_PATH, VENDOR_NAME, "reports", "report.xlsx")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--start-maximized"])
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            
            # Login
            page.goto(LOGIN_URL)
            page.locator("#email").fill(email)
            page.locator("input[type='password']").fill(password)
            page.locator("button[type='submit']").click()
            page.wait_for_url('https://vendor.elitewherego.com/')
            page.goto(MAIN_URL)

            # Read Excel
            if not os.path.exists(excel_path):
                raise FileNotFoundError(f"Excel file not found at {excel_path}")
            df = pd.read_excel(excel_path)

            for _, row in df.iterrows():
                item_name = str(row['title'])
                normalized_name = normalize_text(item_name)
                item_category = normalize_text(str(row['CategoryName']))
                item_price = str(clean_price(row['price']))
                image_file_name = str(row['Image_File_Name'])
                image_base_name = os.path.splitext(image_file_name)[0]
                search_pattern = os.path.join(folder_path, image_base_name + '.*')

                matching_files = glob.glob(search_pattern)

                if not matching_files:
                    print(f"[{volume}] Image not found: {image_file_name}")
                    continue
                
                image_path = matching_files[0] 

                self.update_state(state='PROGRESS', meta={'current_item': item_name})

                try:
                    photos = images_counter.count_images(folder_path)
                    images_counter.update_remaining_photos(volume, photos)
                    print(f"[{volume}] Images remaining: {photos}")

                    # Search item logic... (rest of your original code)
                    search_box = page.locator("#search")
                    search_box.fill(item_name)
                    try:
                        page.wait_for_function("document.documentElement.classList.contains('nprogress-busy')", timeout=2000)
                        page.wait_for_function("!document.documentElement.classList.contains('nprogress-busy')")
                    except:
                        page.wait_for_timeout(3000)

                    table = page.locator("table.w-full.whitespace-nowrap")
                    edit_buttons = table.locator("a[href^='https://vendor.elitewherego.com/items/']").all()
                    if not edit_buttons:
                        continue

                    table_rows = table.locator("tr.hover\\:bg-gray-100.focus-within\\:bg-gray-100").all()
                    e_buttons = [
                        {
                            "link": edit_buttons[i].get_attribute('href'),
                            "category": normalize_text(table_rows[i].locator("td:nth-child(1)").text_content()),
                            "title": normalize_text(table_rows[i].locator("td:nth-child(2)").text_content()),
                            "price": table_rows[i].locator("td:nth-child(3)").text_content()
                        }
                        for i in range(len(edit_buttons))
                    ]
                    
                    item_found = False
                    for i in range(len(table_rows)):
                        if e_buttons[i]['title'] == normalized_name and e_buttons[i]['category'] == item_category and e_buttons[i]['price'] == item_price:
                            item_found = True
                            page.goto(e_buttons[i]['link'])
                            file_input = page.locator("input[type='file']")
                            file_input.set_input_files(image_path)
                            update_button = page.locator("button[type='submit']")
                            update_button.wait_for(state="visible")
                            update_button.click()
                            page.wait_for_selector(
                                "div.bg-green-500.text-white.px-4.mb-8.flex.items-center.justify-between.rounded.max-w-3xl",
                                state="attached",
                                timeout=10000
                            )
                            break
                    
                    if item_found:
                        os.remove(image_path)
                        print(f"[{volume}] Uploaded and removed: {image_file_name}")

                    page.goto(MAIN_URL)
                
                except Exception as e:
                    print(f"[{volume}] Error processing item {item_name}: {e}")
                    continue

            final_photos = images_counter.count_images(folder_path)
            images_counter.update_remaining_photos(volume, final_photos)
            print(f"[{volume}] ✅ Completed. Final remaining: {final_photos}")

    except Exception as e:
        print(f"[{volume}] ❌ Task failed with error: {e}")
        # Retry the task on failure
        self.retry(exc=e)
    finally:
        browser.close()

@shared_task
def start_processing_all_volumes(email, password):
    """
    Main entry point to start the entire process.
    This task splits images and then dispatches individual tasks for each volume.
    """
    # Define paths relative to the shared base path
    base_folder_path = os.path.join(BASE_PATH, VENDOR_NAME)
    customized_folder_path = os.path.join(base_folder_path, "customized")

    # Split images if necessary
    required_folders = [f"volume{i}" for i in range(1, 5)]
    folder = Path(customized_folder_path)
    if not all((folder / vol).is_dir() for vol in required_folders):
        print("Splitting images into volumes...")
        split_images.split_images_into_volumes(customized_folder_path)

    # Use a Celery group to run tasks in parallel
    task_group = group(
        process_vendor_volume.s(email, password, os.path.join(customized_folder_path, volume), volume)
        for volume in required_folders
    )
    
    # You can get the result of the group to see if all tasks succeeded
    result = task_group.apply_async()
    return {"message": "Processing started", "task_id": result.id}