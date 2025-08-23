from pathlib import Path

volume_labels = {}

volumes_remaining = {'volume1': 0, 'volume2': 0, 'volume3': 0, 'volume4': 0}

def count_images(directory):
    # directory = directory.replace('/','\\')
    path = Path(directory)
    image_extensions = ['.jpg', '.jpeg', '.png']
    image_count = sum(1 for file in path.iterdir() if file.suffix.lower() in image_extensions)
    return image_count


def update_remaining_photos(volume, count):
    volumes_remaining[volume] = count