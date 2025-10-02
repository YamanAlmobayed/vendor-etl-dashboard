from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
import logging
import redis

from utils.helper_methods import remove_paths

from .tasks.categories_scraper import scrape_menu_categories
from utils.helper_methods import check_vendor_zip
from django.core.files.storage import default_storage
import os
from .tasks.items_downloader import scrape_menu_items
from pathlib import Path
from utils.sanitizers import replace_space_with_underscore
from .tasks.addons_scraper import scrape_menu_addons 

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
            "download_link": f'{os.getenv("BASE_URL")}{zip_file_path}',
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
def start_addons_scraper(request):
    """Start addons scraper task."""
    validation_error = validate_required_fields(
        request.data, 
        ['vendor_name']
    )
    if validation_error:
        return Response(validation_error["error"], status=validation_error["status"])

    sanitized_vendor_name = replace_space_with_underscore(request.data["vendor_name"])

    try:
        vendor_url = request.data.get("vendor_url")
        if not vendor_url:
            return Response(
                {'error': 'missing vendor_url'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        task = scrape_menu_addons.delay(
            vendor_url, 
            sanitized_vendor_name
        )
        return create_task_response(task.id, "addons scraping started")
            
    except Exception as e:
        return handle_task_start_error(e, task_name="items scraper")