from celery import shared_task
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
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
import time
from celery import current_app
from utils.file_cleanup_manager import FileCleanupManager

logger = logging.getLogger(__name__)

LOGIN_URL = "https://vendor.elitewherego.com/login"
MAIN_URL = "https://vendor.elitewherego.com/items"
        
def process_volume_thread(vendor_email, vendor_password, folder_path, volume_folder_path, volume, stop_event, task_id):
    """
    Process a single volume in a thread
    """
    thread_id = threading.current_thread().ident
    logger.info(f"[Thread {thread_id} | Volume {volume}] Started processing")
    
    excel_path = os.path.join(folder_path, "reports", "report.xlsx")
    browser = None
    
    try:
        # Check for stop signal
        if stop_event.is_set():
            logger.warning(f"[Thread {thread_id} | Volume {volume}] Stop signal detected. Exiting.")
            return {"status": "terminated", "volume": volume}
        
        with sync_playwright() as p:
            logger.debug(f"[Thread {thread_id} | Volume {volume}] Launching browser")
            browser = p.chromium.launch(headless=True, args=["--start-maximized"])
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            
            # Login
            logger.info(f"[Thread {thread_id} | Volume {volume}] Logging in vendor")
            page.goto(LOGIN_URL)
            page.locator("#email").fill(vendor_email)
            page.locator("input[type='password']").fill(vendor_password)
            page.locator("button[type='submit']").click()
            page.wait_for_url('https://vendor.elitewherego.com/')
            page.goto(MAIN_URL)
            logger.info(f"[Thread {thread_id} | Volume {volume}] Login successful")
            
            # Read Excel
            if not os.path.exists(excel_path):
                raise FileNotFoundError(f"Excel file not found at {excel_path}")
            df = pd.read_excel(excel_path)
            logger.info(f"[Thread {thread_id} | Volume {volume}] Loaded {len(df)} rows from Excel")
            
            processed_items = 0
            check_count = 0
            
            for idx, row in df.iterrows():
                check_count += 1
                
                # Check stop signal every 3 rows or at start of each row
                if check_count % 3 == 0 or check_count == 1:
                    if stop_event.is_set():
                        logger.warning(f"[Thread {thread_id} | Volume {volume}] Stop signal detected at row {idx}. Exiting.")
                        return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                    
                    # Additional check: verify main task hasn't been revoked
                    try:
                        from celery.result import AsyncResult
                        if AsyncResult(task_id).state == "REVOKED":
                            logger.warning(f"[Thread {thread_id} | Volume {volume}] Main task revoked at row {idx}. Exiting.")
                            return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                    except:
                        pass  # Continue if we can't check task state
                
                item_name = str(row['title'])
                normalized_name = normalize_text(item_name)
                item_category = normalize_text(str(row['CategoryName']))
                item_price = str(clean_price(row['price']))
                image_file_name = str(row['Image_File_Name'])
                image_base_name = os.path.splitext(image_file_name)[0]
                search_pattern = os.path.join(volume_folder_path, image_base_name + '.*')
                
                matching_files = glob.glob(search_pattern)
                if not matching_files:
                    #logger.warning(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Image not found: {image_file_name}")
                    continue
                
                image_path = matching_files[0]
                #logger.debug(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Found image {image_file_name}")
                
                try:
                    # Check stop signal before expensive operations
                    if stop_event.is_set():
                        logger.warning(f"[Thread {thread_id} | Volume {volume}] Stop signal detected before processing item. Exiting.")
                        return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                    
                    photos = images_counter.count_images(volume_folder_path)
                    images_counter.update_remaining_photos(volume, photos)
                    logger.info(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Remaining images: {photos}")
                    
                    # Search and upload logic
                    search_box = page.locator("#search")
                    search_box.fill(item_name)
                    
                    # Check stop signal before waiting
                    if stop_event.is_set():
                        return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                    
                    try:
                        page.wait_for_function("document.documentElement.classList.contains('nprogress-busy')", timeout=2000)
                        page.wait_for_function("!document.documentElement.classList.contains('nprogress-busy')")
                    except:
                        page.wait_for_timeout(3000)
                    
                    # Check stop signal before table processing
                    if stop_event.is_set():
                        return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                    
                    table = page.locator("table.w-full.whitespace-nowrap")
                    edit_buttons = table.locator("a[href^='https://vendor.elitewherego.com/items/']").all()
                    if not edit_buttons:
                        logger.debug(f"[Thread {thread_id} | Volume {volume}] Row {idx} - No edit buttons found")
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
                        # Check stop signal before processing each table row
                        if stop_event.is_set():
                            return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                            
                        if e_buttons[i]['title'] == normalized_name and e_buttons[i]['category'] == item_category and e_buttons[i]['price'] == item_price:
                            item_found = True
                 #           logger.info(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Match found for item '{item_name}'")
                            page.goto(e_buttons[i]['link'])
                            
                            # Check stop signal before file upload
                            if stop_event.is_set():
                                return {"status": "terminated", "volume": volume, "processed_items": processed_items}
                            
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
                        processed_items += 1
                        logger.info(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Uploaded & removed {image_file_name}")
                    
                    page.goto(MAIN_URL)
                    
                except Exception as e:
                    logger.error(f"[Thread {thread_id} | Volume {volume}] Row {idx} - Error processing item '{item_name}': {e}")
                    continue
            
            final_photos = images_counter.count_images(volume_folder_path)
            images_counter.update_remaining_photos(volume, final_photos)
            logger.info(f"[Thread {thread_id} | Volume {volume}] Completed. Processed {processed_items} items. Final remaining: {final_photos}")
            
            return {
                "status": "completed",
                "volume": volume,
                "processed_items": processed_items,
                "final_photos": final_photos
            }
    
    except Exception as e:
        logger.error(f"[Thread {thread_id} | Volume {volume}] Failed with error: {e}")
        return {"status": "failed", "volume": volume}
    
    finally:
        if browser:
            try:
                browser.close()
                logger.debug(f"[Thread {thread_id} | Volume {volume}] Browser closed")
            except Exception as e:
                logger.debug(f"[Thread {thread_id} | Volume {volume}] Failed to close browser: {e}")

@shared_task(bind=True, name="auto_upload" ,acks_late=True ,reject_on_worker_lost=True, task_track_started=True, base=Singleton, raise_on_duplicate=True, unique_on=['vendor_email'])
def image_uploader(self, vendor_email, vendor_password, folder_path, zip_file_path):
    logger.info("Task started")
    task_id = self.request.id
    # Create a shared stop event that all threads will reference
    stop_event = threading.Event()
    
    # Store stop_event in task instance so threads can check task revocation too
    self._stop_event = stop_event
    
    try:
        logger.info(f"[Task {task_id}] Starting image uploader for {vendor_email}")
        
        self.update_state(state='PROGRESS', meta={'message': 'Extracting files...'})
        
        # Extract files
        zip_file_extractor(zip_file_path)
        customized_folder_path = os.path.join(folder_path, "customized")
        
        # Split images if necessary
        required_folders = [f"volume{i}" for i in range(1, 5)]
        folder = Path(customized_folder_path)
        if not all((folder / vol).is_dir() for vol in required_folders):
            self.update_state(state='PROGRESS', meta={'message': 'Splitting images into volumes...'})
            split_images.split_images_into_volumes(customized_folder_path)
        self.update_state(state='PROGRESS', meta={'message': 'Starting volume processing threads...'})
        
        FileCleanupManager.register_files_for_cleanup(task_id, [
        zip_file_path,        # a file you’ll create later
        folder_path,        # whole directory to nuke
             ])
        
        # Process volumes using ThreadPoolExecutor
        results = []
        completed = failed = terminated = 0
        
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="VolumeProcessor") as executor:
            # Submit all volume processing tasks
            future_to_volume = {}
            for volume in required_folders:
                volume_folder_path = os.path.join(customized_folder_path, volume)
                future = executor.submit(
                    process_volume_thread,
                    vendor_email,
                    vendor_password,
                    folder_path,
                    volume_folder_path,
                    volume,
                    stop_event,  # Pass the same stop_event object to all threads
                    task_id
                )
                future_to_volume[future] = volume
            logger.info(f"[Task {task_id}] Started {len(future_to_volume)} volume processing threads")
            
            # Monitor thread completion with periodic revocation checks
            completed_futures = 0
            total_futures = len(future_to_volume)
            while completed_futures < total_futures:
                try:
                    if check_revocation(self):
                        logger.warning(f"[Task {task_id}] Task revoked. Signaling threads to stop.")
                        # Cancel remaining futures
                        for remaining_future in future_to_volume:
                            if not remaining_future.done():
                                remaining_future.cancel()
                        raise TaskRevokedError("Task was revoked")
                except TaskRevokedError:
                        logger.info(f"[Task {task_id}] Task terminated gracefully")
                        stop_event.set()
                        cleanup_task_files.apply_async(args=[task_id], countdown=5)
                        return {"status": "terminated", "message": "Task was terminated"}  
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
                            
                            logger.info(f"[Task {task_id}] Volume {volume} finished with status: {result.get('status')}")
                            
                        except Exception as e:
                            logger.error(f"[Task {task_id}] Volume {volume} failed: {e}")
                            results.append({"status": "failed", "volume": volume, "error": str(e)})
                            failed += 1
                        
                        # Update progress
                        total_finished = completed + failed + terminated
                        progress_percentage = (total_finished / len(required_folders)) * 100
                        self.update_state(
                            state='PROGRESS',
                            meta={
                                'message': f'Processing volumes... ({total_finished}/{len(required_folders)} completed)',
                                'completed': completed,
                                'failed': failed,
                                'terminated': terminated,
                                'total': len(required_folders),
                                'progress': progress_percentage,
                                'current': total_finished,
                                'percentage': int(progress_percentage)
                            }
                        )
                        break
                        
                except TimeoutError:
                    continue
        
        # Determine final status
        if terminated == len(required_folders):
            final_status = "terminated"
            status_msg = "All volume processing was terminated"
        elif completed == len(required_folders):
            final_status = "completed"
            status_msg = "All volume processing completed successfully"
        else:
            final_status = "partial"
            status_msg = f"Completed: {completed}, Failed: {failed}, Terminated: {terminated}"
        
        # Schedule file cleanup
        try:
            cleanup_task_files.apply_async(args=[task_id], countdown=5)
        except:
            pass
        return {
            "message": status_msg,
            "status": final_status,
            "completed": completed,
            "failed": failed,
            "terminated": terminated,
            "results": results,
        }
    
    except Exception as e:
        logger.info(f"[Task {task_id}] Failed: {e}")
        
        try:
            cleanup_task_files.apply_async(args=[task_id], countdown=5)
        except:
            pass
        
        return {"message": f"Failed: {str(e)}", "status": "error"}
    
def check_revocation(self):
    """Comprehensive revocation check"""
    task_id = self.request.id
    
    try:
        # Method 1: Check the control inspect
        inspect = current_app.control.inspect()
        revoked_tasks = inspect.revoked()
        
        # Check if task is in revoked list
        if revoked_tasks:
            for worker, tasks in revoked_tasks.items():
                if task_id in tasks:
                    logger.warning(f"[Task {task_id}] Found in revoked tasks list")
                    return True
        
        # Method 2: Try to update state (will fail if revoked)
        #self.update_state(state='PROGRESS', meta={'revocation_check': time.time()})
        #
        ## Method 3: Check AsyncResult state
        #result = AsyncResult(task_id)
        #if result.state == "REVOKED":
        #    print('method 2 revoked')
        #    return True
            
        return False
        
    except Exception as e:
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ['revoked', 'terminated', 'cancelled']):
            logger.warning(f"[Task {task_id}] Revocation detected via exception: {e}")
            return True
        # Re-raise if it's not a revocation-related error
        logger.error(f"[Task {task_id}] Unexpected error in revocation check: {e}")
        return False
