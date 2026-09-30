import requests
from scp.security.url_safety import validate_url
def fetch(url):
    validate_url(url)
    s = requests.Session()
    return s.get(url)
