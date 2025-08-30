import os
import shutil
import redis
from celery import shared_task
from celery.result import AsyncResult
from celery.signals import task_success, task_failure, task_revoked
import logging
import time

logger = logging.getLogger(__name__)
redis_client = redis.StrictRedis(host='redis', port=6379, db=0, decode_responses=True)

class FileCleanupManager:
    """
    Manages file cleanup for tasks
    """
    
    @staticmethod
    def register_files_for_cleanup(task_id, files_to_cleanup):
        """
        Register files/folders that should be cleaned up when task completes
        
        Args:
            task_id: The main task ID
            files_to_cleanup: List of file/folder paths to cleanup
        """
        try:
            cleanup_key = f"cleanup:{task_id}"
            for file_path in files_to_cleanup:
                redis_client.sadd(cleanup_key, file_path)
            redis_client.expire(cleanup_key, 86400)  # 24 hours expiry
            logger.info(f"Registered {len(files_to_cleanup)} files for cleanup: {task_id}")
        except Exception as e:
            logger.error(f"Error registering cleanup files: {e}")
    
    @staticmethod
    def get_cleanup_files(task_id):
        """
        Get list of files to cleanup for a task
        """
        try:
            cleanup_key = f"cleanup:{task_id}"
            return list(redis_client.smembers(cleanup_key))
        except Exception as e:
            logger.error(f"Error getting cleanup files: {e}")
            return []
    
    @staticmethod
    def cleanup_files(task_id, force=False):
        """
        Clean up files associated with a task
        
        Args:
            task_id: The task ID
            force: Force cleanup even if task is still running
        """
        try:
            files_to_cleanup = FileCleanupManager.get_cleanup_files(task_id)
            if not files_to_cleanup:
                logger.info(f"No files to cleanup for task: {task_id}")
                return {"cleaned": 0, "errors": 0}
            
            # Check if task is still running (unless forced)
            if not force:
                if redis_client.scard(f"subtasks:{task_id}") > 0:
                    # Check if any subtasks are still running
                    subtask_ids = redis_client.smembers(f"subtasks:{task_id}")
                    running_tasks = 0
                    for subtask_id in subtask_ids:
                        try:
                            result = AsyncResult(subtask_id)
                            if result.state in ['PENDING', 'STARTED', 'PROGRESS']:
                                running_tasks += 1
                        except:
                            pass
                    
                    if running_tasks > 0:
                        logger.info(f"Task {task_id} still has {running_tasks} running subtasks, skipping cleanup")
                        return {"cleaned": 0, "errors": 0, "skipped": "tasks_still_running"}
            
            cleaned_count = 0
            error_count = 0
            
            for file_path in files_to_cleanup:
                try:
                    if os.path.exists(file_path):
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logger.info(f"Removed file: {file_path}")
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                            logger.info(f"Removed directory: {file_path}")
                        cleaned_count += 1
                    else:
                        logger.info(f"File/folder already removed or doesn't exist: {file_path}")
                        cleaned_count += 1
                except Exception as e:
                    logger.error(f"Error removing {file_path}: {e}")
                    error_count += 1
            
            # Clean up Redis keys
            cleanup_key = f"cleanup:{task_id}"
            redis_client.delete(cleanup_key)
            
            logger.info(f"Cleanup completed for task {task_id}: {cleaned_count} cleaned, {error_count} errors")
            return {"cleaned": cleaned_count, "errors": error_count}
            
        except Exception as e:
            logger.error(f"Error during cleanup for task {task_id}: {e}")
            return {"cleaned": 0, "errors": 1, "error": str(e)}

@shared_task
def cleanup_task_files(task_id, force=False):
    return FileCleanupManager.cleanup_files(task_id, force=force)