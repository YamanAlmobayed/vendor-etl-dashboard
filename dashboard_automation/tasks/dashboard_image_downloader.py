import os
from pathlib import Path
from celery import shared_task
import logging
import shutil
from playwright.sync_api import sync_playwright
from utils.image_downloader import download_image, get_hashed_filename
from utils.sanitizers import replace_slash_with_space
from utils.excel_handlers import append_to_excel
from utils.helper_methods import perform_vendor_login

logger = logging.getLogger(__name__)

@shared_task(bind=True, name='dashboard_image_downloader')
def dashboard_image_downloader(self, vendor_name, vendor_email, vendor_password):
    """Download dashboard images and create report."""
    logger.info(f"Dashboard image downloader task started for {vendor_name}")
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Starting image download process'})
        
        images = _scrape_vendor_images(self, vendor_email, vendor_password)
        
        scrapped_dir = Path('scrapped_data')
        download_path = scrapped_dir / vendor_name / 'images'
        report_path = scrapped_dir / vendor_name / 'reports'
        download_path.mkdir(parents=True, exist_ok=True)
        report_path.mkdir(parents=True, exist_ok=True)
        
        _download_images_and_create_reports(images, download_path, report_path)
        
        return _create_vendor_zip(scrapped_dir, vendor_name)
        
    except Exception as e:
        logger.error(f"Dashboard image downloader task failed: {e}")
        return {'status': 'error', 'message': f'An error occurred while downloading the vendor {vendor_name}'}

def _scrape_vendor_images(self, vendor_email, vendor_password):
    """Scrape images from vendor dashboard."""
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/items"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        try:
            perform_vendor_login(page, LOGIN_URL, vendor_email, vendor_password)
            page.goto(MAIN_URL)
            page.wait_for_selector('table.w-full.whitespace-nowrap')

            images = []
            next_link = MAIN_URL

            while next_link:
                page.goto(next_link)
                page.wait_for_selector('table.w-full.whitespace-nowrap')

                next_link = _get_next_page_link(page)
                table = page.locator("table.w-full.whitespace-nowrap")
                table_rows = table.locator("tr.hover\\:bg-gray-100.focus-within\\:bg-gray-100").all()

                image_list = []
                for row in table_rows:
                    category = row.locator("td:nth-child(1)").text_content()
                    title = row.locator("td:nth-child(2)").text_content()
                    price = row.locator("td:nth-child(3)").text_content()
                    image_url = row.locator("td:nth-child(4) a").get_attribute('href')
                    
                    image_file_name = None
                    if image_url:
                        image_file_name = get_hashed_filename(
                            replace_slash_with_space(title), 
                            image_url, 
                            category
                        )
                        
                    image_list.append({
                        "CategoryName": category,
                        "title": title,
                        "price": price,
                        "image_url": image_url,
                        "Image_File_Name": image_file_name
                    })
                
                images.append(image_list)

            return images
            
        finally:
            browser.close()

def _get_next_page_link(page):
    """Get next page link if available."""
    try:
        return page.locator("a", has_text="Next »").get_attribute("href")
    except:
        return None

def _download_images_and_create_reports(images, download_path, report_path):
    """Download images and create Excel reports."""
    for tab_images in images:
        for image in tab_images:
            if image["image_url"]:
                download_image(image["Image_File_Name"], image["image_url"], download_path)
            
    for tab_items in images:
        for item in tab_items:
            append_to_excel(
                os.path.join(report_path, "report.xlsx"),
                excel_attributes=item
            )

def _create_vendor_zip(scrapped_dir, vendor_name):
    """Create zip file of vendor data."""
    try:
        zip_path = os.path.join(scrapped_dir, vendor_name)
        shutil.make_archive(zip_path, 'zip', zip_path)
        shutil.rmtree(zip_path)
        
        return {
            'status': 'completed',
            'message': f'{vendor_name} have been downloaded successfully',
            'download_link': f'{os.getenv("BASE_URL")}{zip_path}.zip'
        }
    except Exception as e:
        logger.error(f"Error creating zip file: {e}")
        raise