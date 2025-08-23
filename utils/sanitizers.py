import re
import pandas as pd


def sanitize_text(text: str, allow_spaces: bool = True, max_length: int = 100) -> str:
    allowed_chars = r"[^a-zA-Z0-9_\-.\\ ]"
    sanitized = re.sub(allowed_chars, "", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    if not allow_spaces:
        sanitized = sanitized.replace(" ", "_")

    buffer_length = 15
    if len(sanitized) > max_length - buffer_length:
        sanitized = sanitized[:max_length - buffer_length].rstrip("_-. ")

    return sanitized
def remove_all_backslashes(text):
    return text.replace('\\', '')

def remove_aed(text):
    text = str(text)
    cleaned_text = re.sub(r'\bAED\b', '', text, flags=re.IGNORECASE)
    return " ".join(cleaned_text.split())

def normalize_text(text):
    return "".join(text.lower().split())


def extract_items_with_addons(file_path):
    df = pd.read_excel(file_path, usecols=[0, 1, 2, 3, 4, 5], names=['category_name', 'item_name', 'addon_category','addon_name', 'addon_price', 'category_status'])
    
    result = {}
    for _, row in df.iterrows():
        item = row['item_name']
        addon_details = [row['category_name'] ,row['addon_category'], row['addon_name'], row['addon_price'], row['category_status']]
        result.setdefault(item, []).append(addon_details)
    
    return result

def replace_slash_with_space(text):
    return text.replace('/', ' ')