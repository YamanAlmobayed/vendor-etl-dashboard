from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.core.files.storage import default_storage
import os
import logging
from pathlib import Path
import redis

from .tasks.auto_approve import auto_approve
from .tasks.dashboard_image_downloader import dashboard_image_downloader
from .tasks.categories_extractor import scrape_categories
from .tasks.image_uploader import image_uploader
from .tasks.categories_creator import create_categories
from .tasks.addon_categories_creator import create_addon_categories
from .tasks.addons_creator import create_addons
from .tasks.addons_items_linker import link_items_addons
from .tasks.match_categories_with_items import match_categories_with_items
from utils.helper_methods import (
    check_vendor_zip, determine_overall_status, get_subtask_statuses, 
    get_lock_info, remove_paths, save_uploaded_file, validate_uploaded_file
)
from utils.file_cleanup_manager import cleanup_task_files
from celery.result import AsyncResult
from celery import states
from celery_singleton.exceptions import DuplicateTaskError
from celery_singleton.util import generate_lock

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

@api_view(['GET'])
def index(request):
    """API index endpoint."""
    return Response({"message": "Welcome to the Dashboard Automation API!"})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_auto_approve(request):
    """Start auto-approve task."""
    validation_error = validate_required_fields(request.data, ['username', 'password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])

    try:
        task = auto_approve.delay('auto_approve', request.data["username"], request.data["password"])
        return Response({"task_id": task.id, "status": "Approving started"})
    except DuplicateTaskError:
        return handle_duplicate_task_error()

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_dashboard_image_downloader(request):
    """Start dashboard image downloader task."""
    validation_error = validate_required_fields(
        request.data, 
        ['vendor_name', 'vendor_email', 'vendor_password']
    )
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])

    scrapped_dir = Path('scrapped_data')
    zip_file_path = scrapped_dir / f"{request.data['vendor_name']}.zip"
    
    if check_vendor_zip(request.data["vendor_name"]):
        return Response({
            "message": "Vendor data already exists.",
            "download_link": f"http://10.39.1.71/{zip_file_path}",
        })
    
    try:
        task = dashboard_image_downloader.delay(
            request.data["vendor_name"], 
            request.data["vendor_email"], 
            request.data["vendor_password"]
        )
        return Response({"task_id": task.id, "status": "Downloading started"})
    except Exception as e:
        return handle_task_start_error(e, task_name="dashboard image downloader")

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_categories_extractor(request):
    """Start categories extractor task."""
    validation_error = validate_required_fields(
        request.data, 
        ['vendor_name', 'vendor_email', 'vendor_password']
    )
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    try:
        task = scrape_categories.delay(
            request.data["vendor_name"], 
            request.data["vendor_email"], 
            request.data["vendor_password"]
        )
        return Response({"task_id": task.id, "status": "Downloading started"})
    except Exception as e:
        return handle_task_start_error(e, task_name="categories extractor")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_image_uploader(request):
    """Start image uploader task with file validation and cleanup."""
    validation_error = validate_required_fields(request.data, ['vendor_email', 'vendor_password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.zip'):
        return Response(
            {'error': 'Invalid file type. Only .zip files are allowed.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    full_path = None
    try:
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        full_path = os.path.join(default_storage.location, file_path)
        folder_path = full_path.removesuffix(".zip")
        
        task = image_uploader.delay(
            request.data["vendor_email"],
            request.data["vendor_password"],
            folder_path,
            full_path,
        )
        
        logger.info(f"Started image uploader task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "File received. Uploading will begin shortly.",
        }, status=status.HTTP_202_ACCEPTED)
    
    except DuplicateTaskError:
        return handle_duplicate_task_error(full_path)
    except Exception as e:
        return handle_task_start_error(e, full_path, "image uploader")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_categories_creator(request):
    """Start categories creator task with Excel file validation."""
    validation_error = validate_required_fields(request.data, ['vendor_email', 'vendor_password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.xlsx'):
        return Response(
            {'error': 'Invalid file type. Only .xlsx files are allowed.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    excel_path = None
    try:
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        excel_path = os.path.join(default_storage.location, file_path)
        
        task = create_categories.delay(
            'create_categories',
            request.data["vendor_email"],
            request.data["vendor_password"],
            excel_path,
        )
        
        logger.info(f"Started categories creation task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "Excel received. Uploading will begin shortly.",
        }, status=status.HTTP_202_ACCEPTED)
    
    except DuplicateTaskError:
        return handle_duplicate_task_error(excel_path)
    except Exception as e:
        return handle_task_start_error(e, excel_path, "categories creator")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_addons_creator(request):
    """Start addons creator task with Excel file validation."""
    validation_error = validate_required_fields(request.data, ['vendor_email', 'vendor_password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.xlsx'):
        return Response(
            {'error': 'Invalid file type. Only .xlsx files are allowed.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    excel_path = None
    try:
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        excel_path = os.path.join(default_storage.location, file_path)
        
        task = create_addons.delay(
            'create_addons',
            request.data["vendor_email"],
            request.data["vendor_password"],
            excel_path,
        )
        
        logger.info(f"Started addons creator task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "Excel received. Uploading will begin shortly.",
        }, status=status.HTTP_202_ACCEPTED)
    
    except DuplicateTaskError:
        return handle_duplicate_task_error(excel_path)
    except Exception as e:
        return handle_task_start_error(e, excel_path, "addons creator")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_addons_items_linker(request):
    """Start addons items linking task with Excel file validation."""
    validation_error = validate_required_fields(request.data, ['vendor_email', 'vendor_password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.xlsx'):
        return Response(
            {'error': 'Invalid file type. Only .xlsx files are allowed.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    excel_path = None
    try:
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        excel_path = os.path.join(default_storage.location, file_path)
        
        task = link_items_addons.delay(
            'link_items_addons',
            request.data["vendor_email"],
            request.data["vendor_password"],
            excel_path,
        )
        
        logger.info(f"Started addons items linking task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "Excel received. Linking will begin shortly.",
        }, status=status.HTTP_202_ACCEPTED)
    
    except DuplicateTaskError:
        return handle_duplicate_task_error(excel_path)
    except Exception as e:
        return handle_task_start_error(e, excel_path, "addons items linking")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_addon_categories_creator(request):
    """Start addon categories creator task with Excel file validation."""
    validation_error = validate_required_fields(request.data, ['vendor_email', 'vendor_password'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    if 'file' not in request.FILES:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
    
    uploaded_file = request.FILES['file']
    if not uploaded_file.name.endswith('.xlsx'):
        return Response(
            {'error': 'Invalid file type. Only .xlsx files are allowed.'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    excel_path = None
    try:
        file_path = default_storage.save(uploaded_file.name, uploaded_file)
        excel_path = os.path.join(default_storage.location, file_path)
        
        task = create_addon_categories.delay(
            'create_addon_categories',
            request.data["vendor_email"],
            request.data["vendor_password"],
            excel_path,
        )
        
        logger.info(f"Started addon categories creation task {task.id} for file: {uploaded_file.name}")
        
        return Response({
            "task_id": task.id,
            "message": "Excel received. Uploading will begin shortly.",
        }, status=status.HTTP_202_ACCEPTED)
    
    except DuplicateTaskError:
        return handle_duplicate_task_error(excel_path)
    except Exception as e:
        return handle_task_start_error(e, excel_path, "addon categories creator")

@api_view(['POST'])
@parser_classes([MultiPartParser])
@permission_classes([IsAuthenticated])
def start_matching_items_with_categories(request):
    """Start categories matching task with multiple Excel files."""
    upload_file_fields = ['categories_file', 'items_file']
    uploaded_files = {}
    saved_file_paths = []
    file_paths = {}
    
    try:
        # Validate required files are present
        for field_name in upload_file_fields:
            if field_name not in request.FILES:
                return Response(
                    {'error': f'Missing required file: {field_name}'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validate each uploaded file
        for field_name in upload_file_fields:
            file_obj = request.FILES[field_name]
            validation_error = validate_uploaded_file(file_obj, field_name, ['.xlsx'])
            if validation_error:
                return Response(
                    {'error': validation_error['error']}, 
                    status=validation_error['status']
                )
            uploaded_files[field_name] = file_obj
        
        # Save uploaded files temporarily
        for field_name, file_obj in uploaded_files.items():
            try:
                file_path = save_uploaded_file(file_obj)
                saved_file_paths.append(file_path)
                file_paths[field_name] = file_path
                logger.info(f"Saved {field_name} to: {file_path}")
            except Exception as e:
                remove_paths(saved_file_paths)
                return Response(
                    {
                        'error': f'Failed to save {field_name}',
                        'message': str(e)
                    }, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        # Start the Celery task
        task = match_categories_with_items.delay(
            file_paths['categories_file'],
            file_paths['items_file'],
            'media/items_with_cat_id.xlsx',
        )
        
        logger.info(
            f"Started categories matching task {task.id} for user {request.user.id}. "
            f"Files: {uploaded_files['categories_file'].name}, {uploaded_files['items_file'].name}"
        )
        
        return Response(
            {
                'task_id': task.id,
                'message': 'Excel files received. Category matching started.',
                'status': 'processing'
            }, 
            status=status.HTTP_202_ACCEPTED
        )
        
    except Exception as e:
        logger.error(
            f"Unexpected error in start_matching_items_with_categories for user {request.user.id}: {e}",
            exc_info=True
        )
        remove_paths(saved_file_paths)
        return Response(
            {
                'error': 'Failed to start processing task',
                'message': 'An unexpected error occurred. Please try again.'
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

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