from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import re
import html


def deliveroo_url_formatter(css_background_image):
    url_pattern = re.compile(r'url\("(.*?)"\)')
    match = url_pattern.search(css_background_image)
    if not match:
        raise ValueError("Invalid CSS background-image format")
    original_url = match.group(1)
    parsed_url = urlparse(original_url)
    query_params = parse_qs(parsed_url.query)
    desired_params = {
        'auto': query_params.get('auto', ['webp'])[0],
        'format': query_params.get('format', ['jpg'])[0],
        'fit': query_params.get('fit', ['crop'])[0],
    }
    
    modified_url = urlunparse((
        parsed_url.scheme,   
        parsed_url.netloc,   
        parsed_url.path,     
        parsed_url.params,   
        urlencode(desired_params),
        parsed_url.fragment
    ))
    return modified_url

def talabat_url_formatter(url):
    question_mark_index = url.find('?')
    if question_mark_index != -1:
        return url[:question_mark_index]
    return url

def noon_url_formatter(style_str):
    decoded = html.unescape(style_str)
    
    match = re.search(r'url\("([^"]+)"\)', decoded)
    if not match:
        return None

    clean_url = match.group(1).split('?')[0]
    return clean_url