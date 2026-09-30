import requests
from scp.security.url_safety import enforce_egress_policy
def fetch(url):
    enforce_egress_policy(url)
    s = requests.Session()
    return s.get(url)
