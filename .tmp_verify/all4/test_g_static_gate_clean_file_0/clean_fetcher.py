from scp.security.url_safety import safe_urlopen
def fetch():
    return safe_urlopen('https://example.com')
