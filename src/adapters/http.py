from curl_cffi import requests
from bs4 import BeautifulSoup

class HttpClient:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.headers = {
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def get(self, url, params=None):
        r = requests.get(
            url,
            params=params,
            timeout=self.timeout,
            impersonate="chrome",
            headers=self.headers,
            allow_redirects=True,
        )
        r.raise_for_status()
        return r

    @staticmethod
    def soup(html):
        return BeautifulSoup(html, "lxml")
