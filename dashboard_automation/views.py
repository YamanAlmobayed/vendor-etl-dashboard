from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from .tasks import auto_approve
from celery.result import AsyncResult
from celery import states

# Create your views here.

@api_view(['GET'])
def index(request):
    return Response({"message": "Welcome to the Dashboard Automation API!"})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_auto_approve(request):
    task = auto_approve.delay(request.data["username"], request.data["password"])
    return Response({"task_id": task.id, "status": "Approving started"})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def task_status_view(request):
    try:
        task_id = request.data.get("task_id")
        if not task_id:
            return Response({"status": "error", "message": "Task ID not provided."}, status=400)

        task_result = AsyncResult(task_id)
        print(task_result.state)
        # Initialize response with basic info
        response = {
            'task_id': task_id,
            'state': task_result.state,
        }

        # Handle different task states
        if task_result.state == states.SUCCESS:
            response['info'] = task_result.get() # Get the successful return value
        elif task_result.state == states.FAILURE:
            # Get exception details without trying to serialize the exception object
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
    try:
        result = AsyncResult(request.data["task_id"])
        if result.state in ['PROGRESS', 'STARTED', 'RETRY', 'RECEIVED']:
            # Revoke the task with terminate=True to hard-kill the process
            result.revoke(terminate=True)
            return Response({'status': 'success', 'message': f'Task terminated.'})
        else:
            return Response({'status': 'error', 'message': f'Task is not in a terminable state.'}, status=400)
    except Exception as e:
        return Response({'status': 'error', 'message': str(e)}, status=500)