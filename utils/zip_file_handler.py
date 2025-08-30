import zipfile
import os

def zip_file_extractor(file_path):
    """
    Celery task to extract a zip file and process its contents.
    """
    try:
        # Create a temporary directory for extraction
        extract_dir = f"{file_path}".removesuffix(".zip")
        os.makedirs(extract_dir, exist_ok=True)

        # Extract the zip file
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    
    except zipfile.BadZipFile:
        print(f"Error: {file_path} is not a valid zip file.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    #finally:
    #    # Clean up: remove the temporary files and directory
    #    if os.path.exists(file_path):
    #        os.remove(file_path)
    #    if os.path.exists(extract_dir):
    #        shutil.rmtree(extract_dir)