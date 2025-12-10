# Add these imports at the top of your tasks.py
from celery import shared_task
from django.conf import settings
import requests
import logging
import io
import zipfile
import base64
import os
import uuid
from celery_singleton import Singleton

logger = logging.getLogger(__name__)
# ComfyUI Configuration
# Configuration
WINDOWS_IP = '10.39.1.19'  # Update with your Windows machine IP
WINDOWS_PORT = 5000
WINDOWS_API_URL = f'http://{WINDOWS_IP}:{WINDOWS_PORT}/process_images'
COMFYUI_WINDOWS_API_URL = f'http://{WINDOWS_IP}:{WINDOWS_PORT}/process_images_comfyui'

# Server configuration for download links
SERVER_BASE_URL = os.getenv("BASE_URL")   # Update with your server IP/domain
CUSTOMIZED_IMAGES_DIR = "/customized_images/"

def process_single_image_to_zip(image_file):
    """Convert single image to zip format"""
    temp_zip = io.BytesIO()
    
    with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(image_file['name'], image_file['content'])
    
    temp_zip.seek(0)
    return temp_zip.getvalue()

def ensure_customized_images_dir():
    """Ensure the customized_images directory exists"""
    # Get the project root directory (where manage.py is located)
    project_root = getattr(settings, 'BASE_DIR', os.getcwd())
    
    # Create the full path for customized_images directory
    customized_dir = os.path.join(project_root, 'customized_images')
    
    # Create directory if it doesn't exist
    if not os.path.exists(customized_dir):
        os.makedirs(customized_dir, exist_ok=True)
        logger.info(f"Created directory: {customized_dir}")
    
    return customized_dir

@shared_task(bind=True, name='outpainting', base=Singleton, raise_on_duplicate=True, unique_on=['task'])
def process_images_comfyui_task(self, task, file_data_b64, file_type, original_filename):
    """
    Celery task to process images via ComfyUI Flask API
    Pipeline: Outpaint -> Upscale -> Resize/Pad
    
    Args:
        file_data_b64: Base64 encoded file content
        file_type: 'zip' or 'image'
        original_filename: Original filename
    """
    try:
        # Update task status
        self.update_state(
            state='PROCESSING',
            meta={'status': 'Preparing file for ComfyUI processing...', 'progress': 5}
        )
        
        # Decode base64 file data
        file_data = base64.b64decode(file_data_b64)
        
        # Prepare file for Flask API
        if file_type == 'image':
            # Convert single image to zip
            image_info = {
                'name': original_filename,
                'content': file_data
            }
            zip_content = process_single_image_to_zip(image_info)
            file_to_send = zip_content
            filename = f"{original_filename}_single.zip"
        else:
            # Already a zip file
            file_to_send = file_data
            filename = original_filename
        
        self.update_state(
            state='PROCESSING',
            meta={'status': 'Sending to ComfyUI pipeline...', 'progress': 15}
        )
        
        # Prepare request to Flask ComfyUI API
        files = {'zip_file': (filename, file_to_send, 'application/zip')}
        
        logger.info(f"Forwarding {file_type} to ComfyUI Flask API: {COMFYUI_WINDOWS_API_URL}")
        
        self.update_state(
            state='PROCESSING',
            meta={'status': 'Running ComfyUI outpainting...', 'progress': 25}
        )
        
        # Make request to Flask ComfyUI API with longer timeout for full pipeline
        response = requests.post(
            COMFYUI_WINDOWS_API_URL,
            files=files,
            stream=True
        )
        
        if response.status_code == 200:
            self.update_state(
                state='PROCESSING',
                meta={'status': 'ComfyUI processing completed, saving file...', 'progress': 90}
            )
            
            logger.info("Successfully received processed images from ComfyUI Flask API")
            
            # Ensure customized_images directory exists
            customized_dir = ensure_customized_images_dir()
            
            # Generate unique filename to avoid conflicts
            base_name = os.path.splitext(original_filename)[0]
            unique_id = str(uuid.uuid4())[:8]  # Short unique identifier
            saved_filename = f"{base_name}_comfyui_processed_{unique_id}.zip"
            
            # Full path to save the file
            file_path = os.path.join(customized_dir, saved_filename)
            
            # Save the processed zip file to disk
            try:
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"Saved ComfyUI processed file to: {file_path}")
            except Exception as e:
                logger.error(f"Failed to save ComfyUI file: {e}")
                raise Exception(f"Failed to save ComfyUI processed file: {e}")
            
            # Create download URL
            download_url = f"{SERVER_BASE_URL}{CUSTOMIZED_IMAGES_DIR}{saved_filename}"
            
            self.update_state(
                state='PROCESSING',
                meta={'status': 'ComfyUI file saved successfully', 'progress': 100}
            )
            
            # Return success with download link
            return {
                'download_link': download_url,
                'progress': 100
            }
        else:
            logger.error(f"ComfyUI Flask API error: {response.status_code} - {response.text}")
            raise Exception(f'ComfyUI Flask API error: {response.status_code} - {response.text}')
            
    except requests.exceptions.Timeout:
        logger.error("Request to ComfyUI Flask API timed out")
        raise Exception('ComfyUI processing timeout: Pipeline is taking longer than expected (>10 minutes)')
        
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to ComfyUI Flask API at {COMFYUI_WINDOWS_API_URL}")
        raise Exception('ComfyUI service unavailable: Could not connect to ComfyUI processing service')
    
    except Exception as e:
        logger.error(f"Error in process_images_comfyui_task: {str(e)}")
        # Update task state to FAILURE
        self.update_state(
            state='FAILURE',
            meta={
                'status': f'ComfyUI processing failed: {str(e)}',
                'error': str(e),
                'progress': 0
            }
        )
        raise