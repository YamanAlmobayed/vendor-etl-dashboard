from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from .tasks.auto_approve import auto_approve
from .tasks.dashboard_image_downloader import dashboard_image_downloader
from .tasks.image_uploader import image_uploader
from utils.helper_methods import check_vendor_zip, determine_overall_status, get_subtask_statuses, get_lock_info
from celery.result import AsyncResult
from celery import states
from celery_singleton.exceptions import DuplicateTaskError
from celery_singleton.util import generate_lock
import redis
from rest_framework import status
from django.core.files.storage import default_storage
import os
import logging

logger = logging.getLogger(__name__)

# Create your views here.

@api_view(['GET'])
def index(request):
    return Response({"message": "Welcome to the Dashboard Automation API!"})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_auto_approve(request):
    required_keys = ['username', 'password',]
    missing_keys = [key for key in required_keys if key not in request.data]

    if missing_keys:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing_keys)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    try:
        task = auto_approve.delay('auto_approve',request.data["username"], request.data["password"])
        return Response({"task_id": task.id, "status": "Approving started"})
    except DuplicateTaskError:
        return Response({"status": "error", "message": "Task Already Running"}, status=409)
    
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_dashboard_image_downloader(request):
    required_keys = ['vendor_name', 'vendor_email', 'vendor_password']
    missing_keys = [key for key in required_keys if key not in request.data]

    if missing_keys:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing_keys)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    if(check_vendor_zip(request.data["vendor_name"])):
        return Response({ "message": "Vendor data already exists.", "download_link": f"http://10.39.1.71/scrapped_data/{request.data['vendor_name']}.zip",})
    task = dashboard_image_downloader.delay(request.data["vendor_name"], request.data["vendor_email"], request.data["vendor_password"])
    return Response({"task_id": task.id, "status": "Downloading started"})

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_image_uploader(request):
    """
    Start image uploader task with automatic cleanup registration
    """
    # Validate required fields
    required_keys = ['vendor_email', 'vendor_password']
    missing_keys = [key for key in required_keys if key not in request.data]
    if missing_keys:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing_keys)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate file upload
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=400)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.zip'):
        return Response({'error': 'Invalid file type. Only .zip files are allowed.'}, status=400)
    
    try:
        # Save the file temporarily
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        full_path = os.path.join(default_storage.location, file_path)
        
        # Prepare folder path (without .zip extension)
        folder_path = full_path.removesuffix(".zip")
        
        # Start the task with cleanup enabled
        task = image_uploader.delay(
                request.data["vendor_email"],
                request.data["vendor_password"],
                folder_path,
                full_path,
                    )
        
        # Log the task start
        logger.info(f"Started image uploader task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "File received. Uploading will begin shortly.",
        }, status=202)
    
    except DuplicateTaskError:
        return Response({"status": "error", "message": "Task Already Running"}, status=409)
    
    except Exception as e:
        logger.error(f"Error starting image uploader task: {e}")
        
        # Try to cleanup the uploaded file if task creation failed
        try:
            if 'full_path' in locals() and os.path.exists(full_path):
                os.remove(full_path)
                logger.info(f"Cleaned up uploaded file after task creation failure: {full_path}")
        except Exception as cleanup_error:
            logger.error(f"Failed to cleanup uploaded file: {cleanup_error}")
        
        return Response({
            "error": "Failed to start processing task",
            "message": str(e)
        }, status=500)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def task_status_view(request):
    required_keys = ['task_id']
    missing_keys = [key for key in required_keys if key not in request.data]
    if missing_keys:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing_keys)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    r = redis.StrictRedis(
        host='redis',
        port=6379,
        db=0,
        decode_responses=True
    )
    
    try:
        task_id = request.data.get("task_id")

        main_task_result = AsyncResult(task_id)
        
        # Initialize response with basic info
        response = {
            'task_id': task_id,
        }
        
        print(f"Main Task ID: {task_id}, State: {main_task_result.state}")
        
        # Check if this task was terminated
        if r.get(f"terminated:{task_id}"):
            response.update({
                'status': 'terminated',
                'message': 'Task was terminated by user',
                'progress': {
                    'current': 0,
                    'total': 0,
                    'percentage': 0
                }
            })
            return Response(response)
        
        # Handle main task states
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
        
        # For successful main task, check subtasks
        elif main_task_result.state == states.SUCCESS:
            try:
                main_task_info = main_task_result.get()
                
                # Get subtask IDs from the main task result or Redis
                subtask_ids = []
                monitor_task_id = None
                
                if isinstance(main_task_info, dict):
                    subtask_ids = main_task_info.get('subtask_ids', [])
                    monitor_task_id = main_task_info.get('monitor_task_id')
                
                # Also try to get from Redis if not in main task result
                if not subtask_ids:
                    subtask_ids = list(r.smembers(f"subtasks:{task_id}"))
                
                if not subtask_ids:
                    # No subtasks found, return main task result
                    response.update({
                        'status': 'completed',
                        'message': main_task_info.get('message', 'Task completed successfully'),
                        'info': main_task_info
                    })
                    
                    # Add download link if available
                    try:
                        if request.data.get("vendor_name"):
                            response['download_link'] = f"http://10.39.1.71/scrapped_data/{request.data['vendor_name']}.zip"
                    except:
                        pass
                    
                    return Response(response)
                
                # Check subtask statuses
                subtask_statuses = get_subtask_statuses(subtask_ids, r)
                
                # Check monitor task status if available
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
                
                # Determine overall status based on subtasks and monitor
                overall_status = determine_overall_status(subtask_statuses, monitor_status)
                
                response.update(overall_status)
                response['subtasks'] = subtask_statuses
                
                if monitor_status:
                    response['monitor_task'] = monitor_status
                
                # Add download link if completed successfully
                if overall_status['status'] == 'completed':
                    try:
                        if request.data.get("vendor_name"):
                            response['download_link'] = f"http://10.39.1.71/scrapped_data/{request.data['vendor_name']}.zip"
                    except:
                        pass
                
                return Response(response)
                
            except Exception as e:
                logger.error(f"Error processing successful main task: {e}")
                response.update({
                    'state': 'ERROR',
                    'status': 'error',
                    'message': f'Error checking task status: {str(e)}'
                })
                return Response(response)
        
        # Handle other states (STARTED, RETRY, etc.)
        else:
            response.update({
                'state': main_task_result.state,
                'status': 'preparing',
                'message': 'Task is being prepared...',
                'info': main_task_result.info
            })
            return Response(response)
    
    except Exception as e:
        logger.error(f"Error in task_status_view: {e}")
        return Response({"status": "error", "message": str(e)}, status=500)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def terminate_task_view(request):
    r = redis.StrictRedis(
        host='redis',
        port=6379,
        db=0,
        decode_responses=True
    )
    try:
        task_id = request.data["task_id"]
        print(f"Starting termination for task {task_id}")
        
        # Revoke the main task
        try:
            result = AsyncResult(task_id)
            if(request.data.get("multithread", False)):
                result.revoke(terminate=False)
            else:
                result.revoke(terminate=True, signal='SIGKILL')
            print(f"Revoked task: {task_id}")
        except Exception as e:
            print(f"Error revoking task {task_id}: {e}")
        
        # Remove singleton lock if it exists
        ##TODO: Update this part to be generic for all tasks
        try:
            task = request.data.get("task")
            unique_on = request.data.get("unique_on")
            if task and unique_on:
                lock_info = get_lock_info(task, unique_on)
                lock_key = generate_lock(
                    lock_info["name"],
                    task_kwargs= lock_info["unique_on"]
                )
                if r.delete(lock_key):
                    print(f"Deleted singleton lock: {lock_key}")
                else:
                    print(f"Singleton lock not found: {lock_key}")
        except Exception as e:
            print(f"Error removing singleton lock: {e}")
    
        
        return Response({
            'status': 'success',
            'message': f'Successfully terminated task {task_id}.',
        })
        
    except Exception as e:
        print(f"Error terminating task: {e}")
        return Response({'status': 'error', 'message': str(e)}, status=500)
    


