import time
from celery import shared_task

@shared_task(bind=True)
def sample_task(self):
    duration = 10
    for i in range(duration):
        time.sleep(1)
        self.update_state(state='PROGRESS', meta={'current': i+1, 'total': duration})
    return {'result': 'Task completed!'}
