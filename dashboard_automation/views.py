from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.decorators import api_view
from .tasks import auto_approve, sample_task
from celery.result import AsyncResult

# Create your views here.


@api_view()
def start_task_view(request):
    task = sample_task.delay()
    return Response({"task_id": task.id})

@api_view(['GET'])
def index(request):
    return Response({"message": "Welcome to the Dashboard Automation API!"})

@api_view(['POST'])
def start_auto_approve(request):
    print('hello world')
    print(request.data)
    task = auto_approve.delay('request.data["username"]', 'request.data["password"]')
    return Response({"task_id": task.id, "status": "Approving started"})

@api_view(['POST'])
def task_status_view(request):
    task_result = AsyncResult(request.data["task_id"])
    response = {
        'task_id': request.data["task_id"],
        'state': task_result.state,
        'info': task_result.info
    }
    return Response(response)