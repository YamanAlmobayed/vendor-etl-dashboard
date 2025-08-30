import os
import netifaces
import logging
from celery.result import AsyncResult
from celery import states

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

def get_lock_info(task, unique_on):
    tasks = {
        "image_approve": {"name":"auto_approve", "unique_on": {"task":unique_on}},
        "image_upload":  {"name":"auto_upload", "unique_on": {"vendor_email":unique_on}},
    }    
    return tasks[task]