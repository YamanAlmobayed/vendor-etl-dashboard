from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from .tasks.auto_approve import auto_approve
from .tasks.dashboard_image_downloader import dashboard_image_downloader
from utils.helper_methods import check_vendor_zip
from celery.result import AsyncResult
from celery import states
from celery_singleton.exceptions import DuplicateTaskError
from celery_singleton.util import generate_lock
import redis
from rest_framework import status


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
@permission_classes([IsAuthenticated])
def task_status_view(request):
    required_keys = ['task_id']
    missing_keys = [key for key in required_keys if key not in request.data]

    if missing_keys:
        return Response(
            {"error": f"Missing required fields: {', '.join(missing_keys)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    try:
        task_id = request.data.get("task_id")
        if not task_id:
            return Response({"status": "error", "message": "Task ID not provided."}, status=400)

        task_result = AsyncResult(task_id)
        # Initialize response with basic info
        response = {
            'task_id': task_id,
            'state': task_result.state,
        }
        print(f"Task ID: {task_id}, State: {task_result.state}")
        # Handle different task states
        if task_result.state == states.SUCCESS:
            response['info'] = task_result.get()
            if request.data["vendor_name"]:
                response['download_link'] = f"http://10.39.1.71/scrapped_data/{request.data['vendor_name']}.zip"
        elif task_result.state == states.FAILURE:
            response['info'] = str(task_result.info)
        elif task_result.state == states.REVOKED:
            response['info'] = "Task was terminated."
        elif task_result.state == states.PENDING:
            response['info'] = "Task is pending."
        else: # Covers states like STARTED, RETRY, etc.
            response['info'] = task_result.info

        return Response(response)

    except Exception as e:
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
        result = AsyncResult(request.data["task_id"])
        if result.state in ['PROGRESS', 'STARTED', 'RETRY', 'RECEIVED']:
            # Revoke the task with terminate=True to hard-kill the process
            result.revoke(terminate=True)
            r.delete(generate_lock('dashboard_automation.tasks.auto_approve', task_kwargs={'task_action': request.data["task_action"]}))
            return Response({'status': 'success', 'message': f'Task terminated.'})
        else:
            return Response({'status': 'error', 'message': f'Task is not in a terminable state.'}, status=400)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=500)