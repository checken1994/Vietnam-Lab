import requests
def fetch(url):
    s = requests.Session()
    ua = s.headers.get('User-Agent')
    sid = s.cookies.get('sid')
    d = {}
    s.mount('https://', object())
    s.close()
    return d.get('k'), ua, sid
