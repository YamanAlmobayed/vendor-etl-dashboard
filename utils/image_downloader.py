import os
import requests
import hashlib

def get_hashed_filename(image_name, image_url, category):
    hash_input = f"{image_name}|{image_url}|{category}"
    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:6]
    return f"{image_name}_{hash_suffix}"

def download_image(title,url, folder_path):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    try:
        response = requests.get(url)
        response.raise_for_status() 
        image_name = f"{title}.jpg" 
        image_path = os.path.join(folder_path, image_name)
        with open(image_path, 'wb') as file:
            file.write(response.content)
    except requests.exceptions.RequestException as e:
         print(f"Failed to download {url}: {e}")
         
