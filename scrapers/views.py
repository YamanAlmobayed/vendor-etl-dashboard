from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
import logging
import redis

from utils.helper_methods import ( 
    get_lock_info, remove_paths)
from utils.file_cleanup_manager import cleanup_task_files
from celery.result import AsyncResult
from celery_singleton.util import generate_lock
from .tasks.categories_scraper import scrape_menu_categories
from utils.helper_methods import check_vendor_zip
from django.core.files.storage import default_storage
import os
from .tasks.items_downloader import scrape_menu_items
from pathlib import Path
from utils.sanitizers import replace_space_with_underscore

logger = logging.getLogger(__name__)

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

def handle_task_start_error(error, file_paths=None, task_name="processing"):
    """Handle task start error with cleanup and logging."""
    if file_paths:
        remove_paths(file_paths if isinstance(file_paths, list) else [file_paths])
    
    logger.error(f"Error starting {task_name} task: {error}")
    return Response(
        {
            "error": f"Failed to start {task_name} task",
            "message": str(error)
        }, 
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )

def create_task_response(task_id, message="Task started successfully"):
    """Create standardized task response."""
    return Response({
        "task_id": task_id, 
        "status": "started",
        "message": message
    }, status=status.HTTP_202_ACCEPTED)

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
            replace_space_with_underscore(request.data["vendor_name"])
        )
        return create_task_response(task.id, "Categories scraping started")
    except Exception as e:
        return handle_task_start_error(e, task_name="categories scraper")

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_items_downloader(request):
    """Start items downloader task."""
    validation_error = validate_required_fields(
        request.data, 
        ['vendor_name']
    )
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])

    sanitized_vendor_name = replace_space_with_underscore(request.data["vendor_name"])

    scrapped_dir = Path('scrapped_data')
    zip_file_path = scrapped_dir / f"{sanitized_vendor_name}.zip"
    
    if check_vendor_zip(sanitized_vendor_name):
        return Response({
            "message": "Vendor data already exists.",
            "download_link": f"http://10.39.1.71/{zip_file_path}",
        })

    try:
        if request.FILES:
            if 'file' not in request.FILES:
                return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

            uploaded_file = request.FILES['file']
            if not uploaded_file.name.endswith('.xlsx'):
                return Response(
                    {'error': 'Invalid file type. Only .xlsx files are allowed.'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            file_path = default_storage.save(uploaded_file.name, uploaded_file)
            full_path = os.path.join(default_storage.location, file_path)        

            task = scrape_menu_items.delay(
                None, 
                sanitized_vendor_name, 
                "multi",
                full_path
            )
            return create_task_response(task.id, "Multi-menu items scraping started")
        else:
            vendor_url = request.data.get("vendor_url")
            if not vendor_url:
                return Response(
                    {'error': 'Either upload a file or provide vendor_url for single menu scraping'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            task = scrape_menu_items.delay(
                vendor_url, 
                sanitized_vendor_name, 
                "single",
            )
            return create_task_response(task.id, "Single-menu items scraping started")
            
    except Exception as e:
        return handle_task_start_error(e, task_name="items scraper")

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def terminate_scraper_task_view(request):
    """Terminate a running scraper task and clean up resources."""
    validation_error = validate_required_fields(request.data, ['task_id'])
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])
    
    try:
        task_id = request.data["task_id"]
        multithread = request.data.get("multithread", False)
        
        logger.info(f"Starting termination for scraper task {task_id}")
        
        try:
            result = AsyncResult(task_id)
            if multithread:
                result.revoke(terminate=False)
            else:
                result.revoke(terminate=True, signal='SIGKILL')
            logger.info(f"Revoked task: {task_id}")
        except Exception as e:
            logger.error(f"Error revoking task {task_id}: {e}")
        
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
        
        try:
            if not multithread:
                cleanup_task_files.apply_async(args=[task_id], countdown=5)
        except Exception as e:
            logger.error(f"Error scheduling cleanup task: {e}")
        
        return Response({
            'status': 'success',
            'message': f'Successfully terminated scraper task {task_id}.',
        })
        
    except Exception as e:
        logger.error(f"Error terminating scraper task: {e}")
        return Response(
            {'status': 'error', 'message': str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )