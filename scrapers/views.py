from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
import logging
import redis

from utils.helper_methods import (
     determine_overall_status, get_subtask_statuses, 
    get_lock_info, remove_paths)
from utils.file_cleanup_manager import cleanup_task_files
from celery.result import AsyncResult
from celery import states
from celery_singleton.util import generate_lock
from .tasks.categories_scraper import scrape_menu_categories

# Create your views here.
logger = logging.getLogger(__name__)

# Redis connection
redis_client = redis.StrictRedis(
    host='redis',
    port=6379,
    db=0,
    decode_responses=True
)

def validate_required_fields(request_data, required_keys):
    """Validate that all required fields are present in request data."""
    missing_keys = [key for key in required_keys if key not in request_data]
    if missing_keys:
        return {
            "error": f"Missing required fields: {', '.join(missing_keys)}",
            "status": status.HTTP_400_BAD_REQUEST
        }
    return None

def handle_duplicate_task_error(file_paths=None):
    """Handle duplicate task error and cleanup files if provided."""
    if file_paths:
        if isinstance(file_paths, list):
            remove_paths(file_paths)
        else:
            remove_paths([file_paths])
    return Response(
        {"status": "error", "message": "Task Already Running"}, 
        status=status.HTTP_409_CONFLICT
    )

def handle_task_start_error(error, file_paths=None, task_name="processing"):
    """Handle task start error with cleanup and logging."""
    if file_paths:
        if isinstance(file_paths, list):
            remove_paths(file_paths)
        else:
            remove_paths([file_paths])
    
    logger.error(f"Error starting {task_name} task: {error}")
    return Response(
        {
            "error": f"Failed to start {task_name} task",
            "message": str(error)
        }, 
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_categories_scraper(request):
    """Start categories extractor task."""
    validation_error = validate_required_fields(
        request.data, 
        ['vendor_name', 'vendor_url']
    )
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    try:
        task = scrape_menu_categories.delay(
            request.data["vendor_url"], 
            request.data["vendor_name"]
        )
        return Response({"task_id": task.id, "status": "Downloading started"})
    except Exception as e:
        return handle_task_start_error(e, task_name="categories scraper")

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def task_status_view(request):
    """Get task status including subtasks and monitor tasks."""
    validation_error = validate_required_fields(request.data, ['task_id'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    try:
        task_id = request.data.get("task_id")
        main_task_result = AsyncResult(task_id)
        
        response = {'task_id': task_id}
        
        logger.debug(f"Main Task ID: {task_id}, State: {main_task_result.state}")
        
        # Check if task was terminated
        if redis_client.get(f"terminated:{task_id}"):
            response.update({
                'status': 'terminated',
                'message': 'Task was terminated by user',
                'progress': {'current': 0, 'total': 0, 'percentage': 0}
            })
            return Response(response)
        
        # Handle different task states
        if main_task_result.state == states.FAILURE:
            response.update({
                'status': 'failed',
                'message': str(main_task_result.info),
                'error': str(main_task_result.info)
            })
            return Response(response)
        
        elif main_task_result.state == states.PENDING:
            response.update({
                'status': 'not_found',
                'message': 'There is no current task with this ID.'
            })
            return Response(response)
        
        elif main_task_result.state == states.REVOKED:
            response.update({
                'status': 'revoked',
                'message': 'Task was revoked/terminated.'
            })
            return Response(response)
        
        elif main_task_result.state == states.SUCCESS:
            try:
                main_task_info = main_task_result.get()
                
                # Get subtask information
                subtask_ids = []
                monitor_task_id = None
                
                if isinstance(main_task_info, dict):
                    subtask_ids = main_task_info.get('subtask_ids', [])
                    monitor_task_id = main_task_info.get('monitor_task_id')
                
                # Try Redis if not in main task result
                if not subtask_ids:
                    subtask_ids = list(redis_client.smembers(f"subtasks:{task_id}"))
                
                if not subtask_ids:
                    # No subtasks, return main task result
                    response.update({
                        'status': 'completed',
                        'message': main_task_info.get('message', 'Task completed successfully'),
                        'info': main_task_info
                    })
                    return Response(response)
                
                # Process subtasks and monitor task
                subtask_statuses = get_subtask_statuses(subtask_ids, redis_client)
                
                monitor_status = None
                if monitor_task_id:
                    monitor_result = AsyncResult(monitor_task_id)
                    monitor_status = {
                        'task_id': monitor_task_id,
                        'state': monitor_result.state,
                        'info': None
                    }
                    
                    if monitor_result.state == states.SUCCESS:
                        try:
                            monitor_status['info'] = monitor_result.get()
                        except Exception as e:
                            logger.warning(f"Error getting monitor task result: {e}")
                    elif monitor_result.state == states.FAILURE:
                        monitor_status['info'] = str(monitor_result.info)
                
                # Determine overall status
                overall_status = determine_overall_status(subtask_statuses, monitor_status)
                
                response.update(overall_status)
                response['subtasks'] = subtask_statuses
                
                if monitor_status:
                    response['monitor_task'] = monitor_status
                
                # Add download link if completed successfully
                if overall_status['status'] == 'completed' and request.data.get("vendor_name"):
                    response['download_link'] = f"http://10.39.1.71/scrapped_data/{request.data['vendor_name']}.zip"
                
                return Response(response)
                
            except Exception as e:
                logger.error(f"Error processing successful main task: {e}")
                response.update({
                    'state': 'ERROR',
                    'status': 'error',
                    'message': f'Error checking task status: {str(e)}'
                })
                return Response(response)
        
        else:
            # Handle other states (STARTED, RETRY, etc.)
            response.update({
                'state': main_task_result.state,
                'status': 'preparing',
                'message': 'Task is being prepared...',
                'info': main_task_result.info
            })
            return Response(response)
    
    except Exception as e:
        logger.error(f"Error in task_status_view: {e}")
        return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def terminate_task_view(request):
    """Terminate a running task and clean up resources."""
    validation_error = validate_required_fields(request.data, ['task_id'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    try:
        task_id = request.data["task_id"]
        multithread = request.data.get("multithread", False)
        
        logger.info(f"Starting termination for task {task_id}")
        
        # Revoke the main task
        try:
            result = AsyncResult(task_id)
            if multithread:
                result.revoke(terminate=False)
            else:
                result.revoke(terminate=True, signal='SIGKILL')
            logger.info(f"Revoked task: {task_id}")
        except Exception as e:
            logger.error(f"Error revoking task {task_id}: {e}")
        
        # Remove singleton lock if provided
        try:
            task = request.data.get("task")
            unique_on = request.data.get("unique_on")
            if task and unique_on:
                lock_info = get_lock_info(task, unique_on)
                lock_key = generate_lock(
                    lock_info["name"],
                    task_kwargs=lock_info["unique_on"]
                )
                if redis_client.delete(lock_key):
                    logger.info(f"Deleted singleton lock: {lock_key}")
                else:
                    logger.warning(f"Singleton lock not found: {lock_key}")
        except Exception as e:
            logger.error(f"Error removing singleton lock: {e}")
        
        # Schedule cleanup task
        try:
            if not multithread:
                cleanup_task_files.apply_async(args=[task_id], countdown=5)
        except Exception as e:
            logger.error(f"Error scheduling cleanup task: {e}")
        
        return Response({
            'status': 'success',
            'message': f'Successfully terminated task {task_id}.',
        })
        
    except Exception as e:
        logger.error(f"Error terminating task: {e}")
        return Response(
            {'status': 'error', 'message': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )