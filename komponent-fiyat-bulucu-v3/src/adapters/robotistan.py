from urllib.parse import quote_plus
from .base import SellerAdapter
from .http import HttpClient
from src.core.models import ProductResult
from src.core.normalizer import parse_price, clean_text
from src.core.matcher import score

class RobotistanAdapter(SellerAdapter):
    name='Robotistan'; base='https://www.robotistan.com'
    def __init__(self): self.http=HttpClient()
    def search(self,q):
        # Robotistan's public site uses search URLs; parse only visible product cards.
        url=f'{self.base}/arama?q={quote_plus(q.mpn)}'
        try:
            soup=self.http.soup(self.http.get(url).text)
            out=[]
            for a in soup.select('a[href]'):
                name=clean_text(a.get_text(' ',strip=True))
                if q.mpn.lower() not in name.lower(): continue
                href=a.get('href',''); href=href if href.startswith('http') else self.base+href
                if len(name)<4: continue
                out.append(ProductResult(self.name,name,href,mpn=q.mpn,package=q.package,quantity=q.quantity,confidence=score(q,ProductResult(self.name,name,href,mpn=q.mpn,package=q.package))))
            return _dedupe(out)
        except Exception as e:
            return [ProductResult(self.name,q.raw,'',mpn=q.mpn,quantity=q.quantity,error=str(e))]

def _dedupe(items):
    seen=set(); out=[]
    for x in items:
        if x.url not in seen: seen.add(x.url); out.append(x)
    return out[:10]
