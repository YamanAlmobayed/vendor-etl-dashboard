import os
import shutil

def split_images_into_volumes(input_folder, volumes=4):
    if not os.path.isdir(input_folder):
        print(f"Error: Input folder '{input_folder}' does not exist.")
        return

    image_extensions = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}
    images = []
    for filename in os.listdir(input_folder):
        file_path = os.path.join(input_folder, filename)
        if os.path.isfile(file_path):
            ext = filename.split('.')[-1].lower()
            if ext in image_extensions:
                images.append(filename)

    if not images:
        print("No image files found in the input folder.")
        return
    
    images.sort()

    total = len(images)
    n = volumes
    chunk_size = total // n
    remainder = total % n
   
    split_images = []
    start = 0
    for i in range(n):
        end = start + chunk_size + (1 if i < remainder else 0)
        split_images.append(images[start:end])
        start = end
   
    output_folders = []
    for i in range(1, n+1):
        folder_name = os.path.join(input_folder, f"volume{i}")
        os.makedirs(folder_name, exist_ok=True)
        output_folders.append(folder_name)

    for folder, chunk in zip(output_folders, split_images):
        for filename in chunk:
            src = os.path.join(input_folder, filename)
            dst = os.path.join(folder, filename)
            shutil.copy(src, dst)
            #print(f"Copied {filename} to {folder}")

    print(f"Successfully split {total} images into {n} volumes.")
