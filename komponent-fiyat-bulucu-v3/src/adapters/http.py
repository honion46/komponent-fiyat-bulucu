from curl_cffi import requests
from bs4 import BeautifulSoup

class HttpClient:
    def __init__(self, timeout=15):
        self.timeout=timeout
    def get(self,url,params=None):
        r=requests.get(url,params=params,timeout=self.timeout,impersonate='chrome',headers={'Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'})
        r.raise_for_status(); return r
    @staticmethod
    def soup(html): return BeautifulSoup(html,'lxml')
