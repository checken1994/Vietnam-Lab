import urllib.request
import requests
def fetch():
    return urllib.request.urlopen('https://example.com')
def post():
    return requests.post('https://example.com', json={})
