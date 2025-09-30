from celery import shared_task
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import pandas as pd
import glob
from utils import images_counter
from utils import split_images
from utils.sanitizers import normalize_text, clean_price
from utils.zip_file_handler import zip_file_extractor
from celery.exceptions import TaskRevokedError
from celery.result import AsyncResult
import logging
from utils.file_cleanup_manager import cleanup_task_files
from celery_singleton import Singleton
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from celery import current_app
from utils.file_cleanup_manager import FileCleanupManager
from utils.helper_methods import perform_vendor_login

logger = logging.getLogger(__name__)

@shared_task(bind=True, name="auto_upload", acks_late=True, reject_on_worker_lost=True, 
             task_track_started=True, base=Singleton, raise_on_duplicate=True, unique_on=['vendor_email'])
def image_uploader(self, vendor_email, vendor_password, folder_path, zip_file_path):
    """Upload images using multithreaded processing."""
    logger.info(f"Image uploader task started for {vendor_email}")
    task_id = self.request.id
    stop_event = threading.Event()
    self._stop_event = stop_event
    
    try:
        self.update_state(state='PROGRESS', meta={'message': 'Extracting files...'})
        
        _prepare_upload_environment(zip_file_path, folder_path, task_id)
        
        customized_folder_path = os.path.join(folder_path, "customized")
        required_folders = [f"volume{i}" for i in range(1, 5)]
        
        _ensure_volume_structure(self, customized_folder_path, required_folders)
        
        results = _process_volumes_with_threads(
            self, vendor_email, vendor_password, folder_path, 
            customized_folder_path, required_folders, stop_event, task_id
        )
        
        return _finalize_upload_results(results, required_folders, task_id)
        
    except TaskRevokedError:
        logger.info(f"Image upload task {task_id} terminated gracefully")
        stop_event.set()
        cleanup_task_files.apply_async(args=[task_id], countdown=5)
        return {"status": "terminated", "message": "Task was terminated"}
    except Exception as e:
        logger.error(f"Image upload task {task_id} failed: {e}")
        cleanup_task_files.apply_async(args=[task_id], countdown=5)
        return {"status": "error", "message": f"Failed: {str(e)}"}

def _prepare_upload_environment(zip_file_path, folder_path, task_id):
    """Prepare the upload environment by extracting files and registering for cleanup."""
    zip_file_extractor(zip_file_path)
    FileCleanupManager.register_files_for_cleanup(task_id, [zip_file_path, folder_path])

def _ensure_volume_structure(self, customized_folder_path, required_folders):
    """Ensure volume structure exists, create if necessary."""
    folder = Path(customized_folder_path)
    if not all((folder / vol).is_dir() for vol in required_folders):
        self.update_state(state='PROGRESS', meta={'message': 'Splitting images into volumes...'})
        split_images.split_images_into_volumes(customized_folder_path)

def _process_volumes_with_threads(self, vendor_email, vendor_password, folder_path, 
                                  customized_folder_path, required_folders, stop_event, task_id):
    """Process all volumes using thread pool executor."""
    self.update_state(state='PROGRESS', meta={'message': 'Starting volume processing threads...'})
    
    results = []
    completed = failed = terminated = 0
    
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="VolumeProcessor") as executor:
        future_to_volume = {}
        for volume in required_folders:
            volume_folder_path = os.path.join(customized_folder_path, volume)
            future = executor.submit(
                _process_volume_thread,
                vendor_email, vendor_password, folder_path,
                volume_folder_path, volume, stop_event, task_id
            )
            future_to_volume[future] = volume
        
        logger.info(f"Started {len(future_to_volume)} volume processing threads")
        
        completed_futures = 0
        total_futures = len(future_to_volume)
        
        while completed_futures < total_futures:
            if _check_task_revocation(self):
                logger.warning(f"Task {task_id} revoked. Signaling threads to stop.")
                stop_event.set()
                for remaining_future in future_to_volume:
                    if not remaining_future.done():
                        remaining_future.cancel()
                raise TaskRevokedError("Task was revoked")
            
            try:
                for future in as_completed(future_to_volume, timeout=10):
                    completed_futures += 1
                    volume = future_to_volume[future]
                    
                    try:
                        result = future.result()
                        results.append(result)
                        
                        if result.get('status') == 'completed':
                            completed += 1
                        elif result.get('status') == 'terminated':
                            terminated += 1
                        else:
                            failed += 1
                        
                        logger.info(f"Volume {volume} finished with status: {result.get('status')}")
                        
                    except Exception as e:
                        logger.error(f"Volume {volume} failed: {e}")
                        results.append({"status": "failed", "volume": volume, "error": str(e)})
                        failed += 1
                    
                    _update_volume_progress(self, completed, failed, terminated, len(required_folders))
                    break
                    
            except TimeoutError:
                continue
    
    return results, completed, failed, terminated

def _process_volume_thread(vendor_email, vendor_password, folder_path, volume_folder_path, 
                          volume, stop_event, task_id):
    """Process a single volume in a thread - MIMICKING WORKING VERSION LOGIC."""
    thread_id = threading.current_thread().ident
    logger.info(f"[Thread {thread_id} | Volume {volume}] Started processing")
    
    excel_path = os.path.join(folder_path, "reports", "report.xlsx")
    LOGIN_URL = "https://vendor.elitewherego.com/login"
    MAIN_URL = "https://vendor.elitewherego.com/items"
    
    try:
        if stop_event.is_set():
            logger.warning(f"[Thread {thread_id} | Volume {volume}] Stop signal detected. Exiting.")
            return {"status": "terminated", "volume": volume}
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--start-maximized"])
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            
            try:
                # Login (same as working version)
                page.goto(LOGIN_URL)
                page.locator("#email").fill(vendor_email)
                page.locator("input[type='password']").fill(vendor_password)
                page.locator("button[type='submit']").click()
                page.wait_for_url('https://vendor.elitewherego.com/')
                page.goto(MAIN_URL)

                # Read Excel ONCE (same as working version)
                if not os.path.exists(excel_path):
                    raise FileNotFoundError(f"Excel file not found at {excel_path}")
                
                df = pd.read_excel(excel_path)
                processed_items = 0
                check_count = 0

                # Process items exactly like working version
                for _, row in df.iterrows():
                    check_count += 1
                    
                    # Periodic stop check (keep for server version)
                    if check_count % 5 == 0:  # Less frequent checks
                        if stop_event.is_set() or _is_task_revoked(task_id):
                            logger.warning(f"[Volume {volume}] Stop signal detected. Exiting.")
                            break
                    
                    # Extract item data (same as working version)
                    item_name = str(row['title'])
                    normalized_name = normalize_text(item_name)
                    item_category = normalize_text(str(row['CategoryName']))
                    item_price = str(clean_price(row['price']))
                    image_file_name = str(row['Image_File_Name'])
                    image_base_name = os.path.splitext(image_file_name)[0]
                    search_pattern = os.path.join(volume_folder_path, image_base_name + '.*')

                    matching_files = glob.glob(search_pattern)
                    if not matching_files:
                        logger.info(f"[{volume}] Image not found: {image_file_name}")
                        continue
                    
                    image_path = matching_files[0]

                    try:
                        # Update image counter (same as working version)
                        photos = images_counter.count_images(volume_folder_path)
                        images_counter.update_remaining_photos(volume, photos)
                        logger.info(f"[{volume}] Images remaining: {photos}")

                        # Search item (EXACTLY same as working version)
                        for i in range(3):
                            try:
                                search_box = page.locator("#search")
                                search_box.fill(item_name)
                                break
                            except:
                                page.reload()
                                continue

                        # Wait pattern (EXACTLY same as working version)
                        try:
                            page.wait_for_function("document.documentElement.classList.contains('nprogress-busy')", timeout=2000)
                            page.wait_for_function("!document.documentElement.classList.contains('nprogress-busy')")
                        except:
                            page.wait_for_timeout(3000)

                        # Table processing (EXACTLY same as working version)
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

                        # Item matching and upload (EXACTLY same as working version)
                        item_found = False
                        for i in range(len(table_rows)):
                            if e_buttons[i]['title'] == normalized_name and e_buttons[i]['category'] == item_category and e_buttons[i]['price'] == item_price:
                                item_found = True
                                page.goto(e_buttons[i]['link'])
                                status_field = page.locator("#status")
                                status_field.wait_for(state="visible")
                                status_field.select_option("Active")
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
                            logger.info(f"[{volume}] Uploaded and removed: {image_file_name}")
                            processed_items += 1

                        # Return to main page (EXACTLY same as working version)
                        page.goto(MAIN_URL)

                    except Exception as e:
                        logger.error(f"[{volume}] Error processing item '{item_name}': {e}")
                        continue

                # Final cleanup (same as working version)
                final_photos = images_counter.count_images(volume_folder_path)
                images_counter.update_remaining_photos(volume, final_photos)
                logger.info(f"[{volume}] ✅ Completed. Processed {processed_items} items. Final remaining: {final_photos}")
                
                return {
                    "status": "completed",
                    "volume": volume,
                    "processed_items": processed_items,
                    "final_photos": final_photos
                }
                
            finally:
                browser.close()
                
    except Exception as e:
        logger.error(f"[Thread {thread_id} | Volume {volume}] Failed with error: {e}")
        return {"status": "failed", "volume": volume, "error": str(e)}

def _check_task_revocation(self):
    """Check if task has been revoked."""
    try:
        task_id = self.request.id
        inspect = current_app.control.inspect()
        revoked_tasks = inspect.revoked()
        
        if revoked_tasks:
            for worker, tasks in revoked_tasks.items():
                if task_id in tasks:
                    return True
        return False
    except Exception as e:
        error_msg = str(e).lower()
        return any(keyword in error_msg for keyword in ['revoked', 'terminated', 'cancelled'])

def _is_task_revoked(task_id):
    """Check if specific task ID is revoked."""
    try:
        result = AsyncResult(task_id)
        return result.state == "REVOKED"
    except:
        return False

def _update_volume_progress(self, completed, failed, terminated, total_volumes):
    """Update task progress for volume processing."""
    total_finished = completed + failed + terminated
    progress_percentage = (total_finished / total_volumes) * 100
    
    self.update_state(
        state='PROGRESS',
        meta={
            'message': f'Processing volumes... ({total_finished}/{total_volumes} completed)',
            'completed': completed,
            'failed': failed,
            'terminated': terminated,
            'total': total_volumes,
            'progress': progress_percentage,
            'current': total_finished,
            'percentage': int(progress_percentage)
        }
    )

def _finalize_upload_results(volume_results, required_folders, task_id):
    """Finalize and return upload results."""
    results, completed, failed, terminated = volume_results
    
    if terminated == len(required_folders):
        final_status = "terminated"
        status_msg = "All volume processing was terminated"
    elif completed == len(required_folders):
        final_status = "completed"
        status_msg = "All volume processing completed successfully"
    else:
        final_status = "partial"
        status_msg = f"Completed: {completed}, Failed: {failed}, Terminated: {terminated}"
    
    cleanup_task_files.apply_async(args=[task_id], countdown=5)
    
    return {
        "status": final_status,
        "message": status_msg,
        "completed": completed,
        "failed": failed,
        "terminated": terminated,
        "results": results,
    }
