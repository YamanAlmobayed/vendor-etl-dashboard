import os
import shutil
import logging
from celery.result import AsyncResult
from celery import states
from pathlib import Path
from typing import List, Union
from rest_framework import status
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

def get_subtask_statuses(subtask_ids, redis_client):
    """
    Get status of all subtasks
    """
    statuses = []
    
    for subtask_id in subtask_ids:
        try:
            subtask_result = AsyncResult(subtask_id)
            subtask_info = {
                'task_id': subtask_id,
                'state': subtask_result.state,
                'progress': None,
                'current_item': None,
                'volume': None
            }
            
            # Get detailed info based on state
            if subtask_result.state == states.SUCCESS:
                try:
                    result_data = subtask_result.get()
                    if isinstance(result_data, dict):
                        subtask_info.update({
                            'volume': result_data.get('volume'),
                            'processed_items': result_data.get('processed_items'),
                            'final_photos': result_data.get('final_photos'),
                            'result': result_data
                        })
                except Exception as e:
                    logger.warning(f"Error getting subtask result for {subtask_id}: {e}")
            
            elif subtask_result.state == states.FAILURE:
                subtask_info['error'] = str(subtask_result.info)
            
            elif subtask_result.state in [states.STARTED, 'PROGRESS']:
                # Try to get progress info
                try:
                    if hasattr(subtask_result, 'info') and isinstance(subtask_result.info, dict):
                        subtask_info.update({
                            'current_item': subtask_result.info.get('current_item'),
                            'processed': subtask_result.info.get('processed', 0),
                            'total': subtask_result.info.get('total', 0)
                        })
                        
                        # Calculate progress percentage
                        if subtask_info['total'] > 0:
                            subtask_info['progress'] = round(
                                (subtask_info['processed'] / subtask_info['total']) * 100, 2
                            )
                except Exception as e:
                    logger.warning(f"Error getting subtask progress for {subtask_id}: {e}")
            
            elif subtask_result.state == states.REVOKED:
                subtask_info['message'] = 'Task was terminated'
            
            statuses.append(subtask_info)
            
        except Exception as e:
            logger.error(f"Error checking subtask {subtask_id}: {e}")
            statuses.append({
                'task_id': subtask_id,
                'state': 'ERROR',
                'error': str(e)
            })
    
    return statuses

def determine_overall_status(subtask_statuses, monitor_status=None):
    """
    Determine overall status based on subtask states
    """
    if not subtask_statuses:
        return {
            'state': 'NO_SUBTASKS',
            'status': 'preparing',
            'message': 'Preparing tasks...'
        }
    
    total_tasks = len(subtask_statuses)
    completed = sum(1 for s in subtask_statuses if s['state'] == states.SUCCESS)
    failed = sum(1 for s in subtask_statuses if s['state'] == states.FAILURE)
    revoked = sum(1 for s in subtask_statuses if s['state'] == states.REVOKED)
    running = sum(1 for s in subtask_statuses if s['state'] in [states.STARTED, 'PROGRESS'])
    pending = sum(1 for s in subtask_statuses if s['state'] == states.PENDING)
    
    # Calculate total progress
    total_progress = 0
    items_processed = 0
    total_items = 0
    
    for s in subtask_statuses:
        if s.get('progress') is not None:
            total_progress += s['progress']
        if s.get('processed') is not None:
            items_processed += s['processed']
        if s.get('total') is not None:
            total_items += s['total']
    
    avg_progress = round(total_progress / total_tasks, 2) if total_tasks > 0 else 0
    
    # Check monitor status for final result
    if monitor_status and monitor_status['state'] == states.SUCCESS:
        monitor_info = monitor_status.get('info', {})
        if isinstance(monitor_info, dict):
            final_status = monitor_info.get('status')
            if final_status == 'completed':
                return {
                    'state': 'COMPLETED',
                    'status': 'completed',
                    'message': '✅ All volume processing tasks completed successfully!',
                    'progress': {
                        'completed': completed,
                        'failed': failed,
                        'revoked': revoked,
                        'total': total_tasks,
                        'percentage': 100,
                        'items_processed': items_processed,
                        'total_items': total_items
                    },
                    'final_result': monitor_info
                }
            elif final_status == 'terminated':
                return {
                    'state': 'TERMINATED',
                    'status': 'terminated',
                    'message': 'All volume processing tasks were terminated',
                    'progress': {
                        'completed': completed,
                        'failed': failed,
                        'revoked': revoked,
                        'total': total_tasks,
                        'percentage': avg_progress,
                        'items_processed': items_processed,
                        'total_items': total_items
                    }
                }
            elif final_status in ['partial', 'mixed']:
                return {
                    'state': 'PARTIAL',
                    'status': 'partial',
                    'message': monitor_info.get('message', 'Some tasks completed with issues'),
                    'progress': {
                        'completed': completed,
                        'failed': failed,
                        'revoked': revoked,
                        'total': total_tasks,
                        'percentage': avg_progress,
                        'items_processed': items_processed,
                        'total_items': total_items
                    },
                    'final_result': monitor_info
                }
    
    # Determine status based on subtask states
    if revoked == total_tasks:
        return {
            'state': 'TERMINATED',
            'status': 'terminated',
            'message': 'All tasks were terminated',
            'progress': {
                'completed': completed,
                'failed': failed,
                'revoked': revoked,
                'total': total_tasks,
                'percentage': 0,
                'items_processed': items_processed,
                'total_items': total_items
            }
        }
    
    if completed == total_tasks:
        return {
            'state': 'COMPLETED',
            'status': 'completed',
            'message': '✅ All volume processing tasks completed successfully!',
            'progress': {
                'completed': completed,
                'failed': failed,
                'revoked': revoked,
                'total': total_tasks,
                'percentage': 100,
                'items_processed': items_processed,
                'total_items': total_items
            }
        }
    
    if failed > 0 and (completed + failed + revoked) == total_tasks:
        return {
            'state': 'PARTIAL',
            'status': 'partial',
            'message': f'Completed with {completed} successful, {failed} failed, {revoked} terminated',
            'progress': {
                'completed': completed,
                'failed': failed,
                'revoked': revoked,
                'total': total_tasks,
                'percentage': avg_progress,
                'items_processed': items_processed,
                'total_items': total_items
            }
        }
    
    if running > 0 or pending > 0:
        return {
            'state': 'RUNNING',
            'status': 'running',
            'message': f'Processing volumes... ({completed}/{total_tasks} completed)',
            'progress': {
                'completed': completed,
                'failed': failed,
                'revoked': revoked,
                'running': running,
                'pending': pending,
                'total': total_tasks,
                'percentage': avg_progress,
                'items_processed': items_processed,
                'total_items': total_items
            }
        }
    
    # Default case
    return {
        'state': 'PREPARING',
        'status': 'preparing',
        'message': 'Preparing volume processing tasks...',
        'progress': {
            'completed': completed,
            'failed': failed,
            'revoked': revoked,
            'total': total_tasks,
            'percentage': 0,
            'items_processed': items_processed,
            'total_items': total_items
        }
    }

def check_vendor_zip(vendor_name):
    base_path = '/scrapped_data/'
    
    full_path = os.path.join(base_path, f'{vendor_name}.zip')
    
    return os.path.isfile(full_path)

def remove_paths(paths: Union[str, List[str]]) -> None:
    """
    Removes one or multiple files/folders (recursively for folders).

    Args:
        paths (Union[str, List[str]]): Path or list of paths to remove.

    Raises:
        FileNotFoundError: If a path does not exist.
        PermissionError: If the program lacks permission.
    """
    if isinstance(paths, str):
        paths = [paths]  # normalize single string into list

    for path in paths:
        target = Path(path)

        if not target.exists():
            logging.warning(f"Path not found: {target}")
            continue

        try:
            if target.is_file():
                target.unlink()
                logging.info(f"File removed: {target}")
            elif target.is_dir():
                shutil.rmtree(target)
                logging.info(f"Folder removed: {target}")
            else:
                logging.error(f"Unsupported path type: {target}")
        except PermissionError as e:
            logging.error(f"Permission denied while removing {target}: {e}")
            raise
        except Exception as e:
            logging.error(f"Error removing {target}: {e}")
            raise

def get_lock_info(task, unique_on):
    tasks = {
        "image_approve": {"name":"auto_approve", "unique_on": {"task":unique_on}},
        "image_upload":  {"name":"auto_upload", "unique_on": {"vendor_email":unique_on}},
        "create_categories":  {"name":"create_categories", "unique_on": {"task":unique_on}},
        "create_addon_categories":  {"name":"create_addon_categories", "unique_on": {"task":unique_on}},
        "create_addons":  {"name":"create_addons", "unique_on": {"task":unique_on}},
        "link_items_addons":  {"name":"link_items_addons", "unique_on": {"task":unique_on}},
    }    
    return tasks[task]

def validate_uploaded_file(file, field_name, allowd_extensions):
    """
    Validate uploaded file type and extension.
    
    Args:
        file: Uploaded file object
        field_name: Name of the form field
        
    Returns:
        dict: Error response if validation fails, None if valid
    """
    if not file:
        return {
            'error': f'No {field_name} uploaded',
            'status': status.HTTP_400_BAD_REQUEST
        }
    
    file_extension = Path(file.name).suffix.lower()
    if file_extension not in allowd_extensions:
        return {
            'error': f'Invalid file type for {field_name}. Only {", ".join(allowd_extensions)} files are allowed.',
            'status': status.HTTP_400_BAD_REQUEST
        }
    
    return None

def save_uploaded_file(file):
    """
    Save uploaded file to storage and return full path.
    
    Args:
        file: Uploaded file object
        
    Returns:
        str: Full path to saved file
        
    Raises:
        Exception: If file saving fails
    """
    try:
        file_path = default_storage.save(file.name, file)
        return os.path.join(default_storage.location, file_path)
    except Exception as e:
        logger.error(f"Failed to save file {file.name}: {e}")
        raise
    
def perform_vendor_login(page, login_url, vendor_email, vendor_password):
    """Perform vendor login with error handling."""
    try:
        page.goto(login_url)
        page.fill("#email", vendor_email)
        page.fill("input[type='password']", vendor_password)
        page.click("button[type='submit']")
        page.wait_for_url('https://vendor.elitewherego.com/')
        logger.info("Vendor login successful")
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise

def update_task_progress(self, current, total, item_type):
    """Update task progress consistently."""
    progress = int((current / total) * 100)
    self.update_state(
        state='PROGRESS',
        meta={
            'current': current,
            'total': total,
            'progress': progress,
            'status': f"Processed {current}/{total} {item_type}"
        }
    )
    
