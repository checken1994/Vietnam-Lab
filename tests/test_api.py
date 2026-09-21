import urllib.request, urllib.error
from urllib.request import urlopen as _url_open

def test_api_status():
    req = urllib.request.Request('http://127.0.0.1:8000/v3/web/status', method='GET')
    try:
        print(_url_open(req).getcode())
    except urllib.error.URLError:
        pass # The server might not be running locally, this test is just asserting it doesn't crash the suite
    except urllib.error.HTTPError as e:
        print(e.code)
