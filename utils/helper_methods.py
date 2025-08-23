import os
import netifaces
import logging

logger = logging.getLogger(__name__)

def check_vendor_zip(vendor_name):
    base_path = '/scrapped_data/'
    
    full_path = os.path.join(base_path, f'{vendor_name}.zip')
    
    return os.path.isfile(full_path)

def get_vm_ip():
    """Returns the IP address of the 'enp0s3' interface."""
    try:
        interfaces = netifaces.interfaces()
        
        if 'enp0s3' in interfaces:
            addresses = netifaces.ifaddresses('enp0s3')
            
            if netifaces.AF_INET in addresses:
                ip_address = addresses[netifaces.AF_INET][0]['addr']
                return ip_address
    except Exception as e:
        logger.error(f"Error: {e}")
        return None