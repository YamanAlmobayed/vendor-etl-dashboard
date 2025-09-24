from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework import status
import zipfile
import logging
from PIL import Image
from .tasks.resize import process_images_task
import base64
from .tasks.outpainting import process_images_comfyui_task

logger = logging.getLogger(__name__)

@api_view(['POST'])
@parser_classes([MultiPartParser])
def process_images_comfyui(request):
    """
    Process images with ComfyUI pipeline - accepts either a zip file or a single image
    Triggers Celery task for ComfyUI processing (outpaint + upscale + resize)
    
    POST: Upload and process images (returns task_id)
    """
    
    try:
        # Check if we have any file uploaded
        if not request.FILES:
            return Response({
                'error': 'No file uploaded',
                'detail': 'Please upload either a zip file containing images or a single image file'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the uploaded file
        uploaded_file = None
        file_type = None
        
        if 'zip_file' in request.FILES:
            uploaded_file = request.FILES['zip_file']
            file_type = 'zip'
        elif 'image' in request.FILES:
            uploaded_file = request.FILES['image']
            file_type = 'image'
        else:
            # Auto-detect file type
            file_keys = list(request.FILES.keys())
            if file_keys:
                uploaded_file = request.FILES[file_keys[0]]
                filename = uploaded_file.name.lower()
                if filename.endswith('.zip'):
                    file_type = 'zip'
                elif any(filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp']):
                    file_type = 'image'
                else:
                    return Response({
                        'error': 'Unsupported file type',
                        'detail': 'Please upload a zip file or an image file (jpg, jpeg, png, webp, bmp)'
                    }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate file size (100MB limit)
        if uploaded_file.size > 100 * 1024 * 1024:
            return Response({
                'error': 'File too large',
                'detail': 'Maximum file size is 100MB'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate file content
        if file_type == 'zip':
            try:
                uploaded_file.seek(0)
                with zipfile.ZipFile(uploaded_file, 'r') as test_zip:
                    image_files = [f for f in test_zip.namelist() 
                                 if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp'))]
                    if not image_files:
                        return Response({
                            'error': 'No image files found in zip',
                            'detail': 'Zip file must contain at least one image file'
                        }, status=status.HTTP_400_BAD_REQUEST)
                uploaded_file.seek(0)
            except zipfile.BadZipFile:
                return Response({
                    'error': 'Invalid zip file',
                    'detail': 'The uploaded file is not a valid zip file'
                }, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Validate image file
            try:
                uploaded_file.seek(0)
                with Image.open(uploaded_file) as img:
                    img.verify()
                uploaded_file.seek(0)
            except Exception:
                return Response({
                    'error': 'Invalid image file',
                    'detail': 'The uploaded file is not a valid image'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Received {file_type} file for ComfyUI processing: {uploaded_file.name}, size: {uploaded_file.size} bytes")
        
        # Read and encode file content as base64
        uploaded_file.seek(0)
        file_content = uploaded_file.read()
        file_content_b64 = base64.b64encode(file_content).decode('utf-8')
        
        # Trigger ComfyUI Celery task
        task = process_images_comfyui_task.delay(
            file_data_b64=file_content_b64,
            file_type=file_type,
            original_filename=uploaded_file.name
        )
        
        logger.info(f"Started ComfyUI Celery task: {task.id}")
        
        return Response({
            'message': 'ComfyUI image processing started',
            'task_id': task.id,
            'status': 'PENDING',
            'processing_type': 'ComfyUI Pipeline (Outpaint + Upscale + Resize)',
            'detail': 'Use the task_id to check processing status and get download link'
        }, status=status.HTTP_202_ACCEPTED)
            
    except Exception as e:
        logger.error(f"Error in process_images_comfyui view: {str(e)}")
        return Response({
            'error': 'Internal server error',
            'detail': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@parser_classes([MultiPartParser])
def process_images(request):
    """
    Process images - accepts either a zip file or a single image
    Triggers Celery task for processing
    
    POST: Upload and process images (returns task_id)
    """
    
    try:
        # Check if we have any file uploaded
        if not request.FILES:
            return Response({
                'error': 'No file uploaded',
                'detail': 'Please upload either a zip file containing images or a single image file'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the uploaded file
        uploaded_file = None
        file_type = None
        
        if 'zip_file' in request.FILES:
            uploaded_file = request.FILES['zip_file']
            file_type = 'zip'
        elif 'image' in request.FILES:
            uploaded_file = request.FILES['image']
            file_type = 'image'
        else:
            # Auto-detect file type
            file_keys = list(request.FILES.keys())
            if file_keys:
                uploaded_file = request.FILES[file_keys[0]]
                filename = uploaded_file.name.lower()
                if filename.endswith('.zip'):
                    file_type = 'zip'
                elif any(filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp']):
                    file_type = 'image'
                else:
                    return Response({
                        'error': 'Unsupported file type',
                        'detail': 'Please upload a zip file or an image file (jpg, jpeg, png, webp, bmp)'
                    }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate file size (100MB limit)
        if uploaded_file.size > 100 * 1024 * 1024:
            return Response({
                'error': 'File too large',
                'detail': 'Maximum file size is 100MB'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate file content
        if file_type == 'zip':
            try:
                uploaded_file.seek(0)
                with zipfile.ZipFile(uploaded_file, 'r') as test_zip:
                    image_files = [f for f in test_zip.namelist() 
                                 if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp'))]
                    if not image_files:
                        return Response({
                            'error': 'No image files found in zip',
                            'detail': 'Zip file must contain at least one image file'
                        }, status=status.HTTP_400_BAD_REQUEST)
                uploaded_file.seek(0)
            except zipfile.BadZipFile:
                return Response({
                    'error': 'Invalid zip file',
                    'detail': 'The uploaded file is not a valid zip file'
                }, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Validate image file
            try:
                uploaded_file.seek(0)
                with Image.open(uploaded_file) as img:
                    img.verify()
                uploaded_file.seek(0)
            except Exception:
                return Response({
                    'error': 'Invalid image file',
                    'detail': 'The uploaded file is not a valid image'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Received {file_type} file: {uploaded_file.name}, size: {uploaded_file.size} bytes")
        
        # Read and encode file content as base64
        uploaded_file.seek(0)
        file_content = uploaded_file.read()
        file_content_b64 = base64.b64encode(file_content).decode('utf-8')
        
        # Trigger Celery task
        task = process_images_task.delay(
            file_data_b64=file_content_b64,
            file_type=file_type,
            original_filename=uploaded_file.name
        )
        
        logger.info(f"Started Celery task: {task.id}")
        
        return Response({
            'message': 'Image processing started',
            'task_id': task.id,
            'status': 'PENDING',
            'detail': 'Use the task_id to check processing status and get download link'
        }, status=status.HTTP_202_ACCEPTED)
            
    except Exception as e:
        logger.error(f"Error in process_images view: {str(e)}")
        return Response({
            'error': 'Internal server error',
            'detail': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
