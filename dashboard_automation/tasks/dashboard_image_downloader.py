import os
from pathlib import Path
from celery import shared_task
import logging
import shutil
from playwright.sync_api import sync_playwright
from utils.image_downloader import download_image, get_hashed_filename
from utils.sanitizers import replace_slash_with_space
from utils.excel_handlers import append_to_excel

logger = logging.getLogger(__name__)

LOGIN_URL = "https://vendor.elitewherego.com/login"
MAIN_URL = "https://vendor.elitewherego.com/items"

def get_next_link(page):
    try:
        return page.locator("a", has_text="Next »").get_attribute("href")
    except:
        return None
    
@shared_task(bind=True, name='dashboard_image_downloader')
def dashboard_image_downloader(self, vendor_name, vendor_email, vendor_password):
    logger.info("Task started")
    logger.info(self)
    logger.info(f"Downloading {vendor_name} started")
    self.update_state(state='PROGRESS')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True, )
        page = context.new_page()

        # Login
        page.goto(LOGIN_URL)
        page.locator("#email").fill(vendor_email)
        page.locator("input[type='password']").fill(vendor_password)
        page.locator("button[type='submit']").click()
        page.wait_for_url('https://vendor.elitewherego.com/')
        page.goto(MAIN_URL)
        page.wait_for_selector('table.w-full.whitespace-nowrap')

        images = []
        next_link = MAIN_URL

        while next_link:
            page.goto(next_link)
            page.wait_for_selector('table.w-full.whitespace-nowrap')

            next_link = get_next_link(page)
            table = page.locator("table.w-full.whitespace-nowrap")
            table_rows = table.locator("tr.hover\\:bg-gray-100.focus-within\\:bg-gray-100").all()

            image_list = []
            for row in table_rows:
                #category = sanitizer.sanitize_text(row.locator("td:nth-child(1)").text_content())
                category = row.locator("td:nth-child(1)").text_content()
                #title = sanitizer.sanitize_text(row.locator("td:nth-child(2)").text_content())
                title = row.locator("td:nth-child(2)").text_content()
                price = row.locator("td:nth-child(3)").text_content()
                image_url = row.locator("td:nth-child(4) a").get_attribute('href')
                if image_url:
                    image_file_name = get_hashed_filename(replace_slash_with_space(title), image_url, category)
                else:
                    image_file_name = None
                    
                image_list.append({
                    "CategoryName": category,
                    "title": title,
                    "price": price,
                    "image_url": image_url,
                    "Image_File_Name": image_file_name
                })
            
            images.append(image_list)

        browser.close()
          # Correctly define the base directory using pathlib
        scrapped_dir = Path('../../scrapped_data')

        # Use the / operator for clean and correct path joining
        download_path = scrapped_dir / vendor_name / 'images'
        report_path = scrapped_dir / vendor_name / 'reports'

        # Create the directories
        download_path.mkdir(parents=True, exist_ok=True)
        report_path.mkdir(parents=True, exist_ok=True)

        for tab_images in images:
            for image in tab_images:
                if image["image_url"]:
                    download_image(image["Image_File_Name"], image["image_url"], download_path)
                
        for tab_items in images:
            for item in tab_items:
                append_to_excel(
                    os.path.join(report_path, f"report.xlsx"),
                    excel_attributes=item
                )
                
        try:
            zip_path = os.path.join(scrapped_dir,vendor_name)
            shutil.make_archive(
                zip_path, 'zip', zip_path
            )

            
            shutil.rmtree(zip_path)
            return {'result': f'{vendor_name} have been downloaded successfully'}
            
    
        except Exception as e:
            logger.error(f"Error creating zip file: {e}")
            return {'result': f'An error has occured while downloading the vendor {vendor_name}'}
        
