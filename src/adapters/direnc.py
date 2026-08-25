import json, re
from urllib.parse import quote_plus, urljoin
from .base import SellerAdapter
from .http import HttpClient
from src.core.models import ProductResult
from src.core.normalizer import parse_price, clean_text
from src.core.matcher import score

class DirencAdapter(SellerAdapter):
    name='Direnc.net'; base='https://www.direnc.net'
    def __init__(self): self.http=HttpClient()

    def search(self,q):
        url=f'{self.base}/arama?q={quote_plus(q.mpn)}'
        try:
            soup=self.http.soup(self.http.get(url).text)
            candidates=[]; seen=set(); needle=q.mpn.lower()
            for a in soup.select('a[href]'):
                name=clean_text(a.get_text(' ',strip=True)); href=urljoin(self.base,a.get('href',''))
                if len(name)<4 or needle not in name.lower() or not href.startswith(self.base): continue
                if '/arama' in href or href in seen: continue
                seen.add(href)
                card=a.find_parent(['li','article','div']); blob=clean_text(card.get_text(' ',strip=True) if card else '')
                candidates.append((name,href,self._price(blob),self._stock(blob)))
            return [self._product(url,q,name,ph,sh) for name,url,ph,sh in candidates[:12]]
        except Exception as e:
            return [ProductResult(self.name,q.raw,url,mpn=q.mpn,quantity=q.quantity,error=str(e))]

    def _product(self,url,q,fallback,price_hint,stock_hint):
        try:
            soup=self.http.soup(self.http.get(url).text); text=clean_text(soup.get_text(' ',strip=True))
            name=self._meta(soup,'og:title') or fallback
            mpn=self._label(text,'Ürün', 'Stok Kodu') or self._jsonld(soup,'sku') or q.mpn
            brand=self._label(text,'Marka/Menşei') or self._jsonld(soup,'brand')
            package=self._label(text,'Ürün Kılıfı') or self._package(text) or q.package
            price=self._jsonld_price(soup) or self._vat_price(text) or price_hint
            stock=self._stock(text) or stock_hint
            in_stock=None if not stock else not any(x in stock.lower() for x in ('stok yok','tükendi','haber ver'))
            p=ProductResult(self.name,name,url,mpn=mpn,manufacturer=brand,package=package,stock_text=stock,in_stock=in_stock,unit_price=price,quantity=q.quantity)
            p.confidence=score(q,p); return p
        except Exception as e:
            return ProductResult(self.name,fallback,url,mpn=q.mpn,package=q.package,quantity=q.quantity,error=str(e))

    @staticmethod
    def _meta(soup,prop):
        t=soup.find('meta',attrs={'property':prop}) or soup.find('meta',attrs={'name':prop})
        return clean_text(t.get('content')) if t else None
    @staticmethod
    def _label(text,*labels):
        for label in labels:
            m=re.search(re.escape(label)+r'\s*[:|-]\s*([^|]{1,100})',text,re.I)
            if m:return clean_text(m.group(1))
        return None
    @staticmethod
    def _vat_price(text):
        m=re.search(r'([\d.]+,\d{2})\s*TL\s*KDV\s+Dahil',text,re.I)
        return parse_price(m.group(1)) if m else None
    @staticmethod
    def _price(text):
        m=re.search(r'([\d.]+,\d{2})\s*TL',text,re.I)
        return parse_price(m.group(1)) if m else None
    @staticmethod
    def _stock(text):
        for p in ('Sepete Ekle','Stokta','Stok Yok','Tükendi','Stok Kodu'):
            if p.lower() in text.lower(): return 'Stokta' if p=='Sepete Ekle' else p
        return None
    @staticmethod
    def _package(text):
        m=re.search(r'\b(DIP|SOIC|SOP|TSSOP|QFN|DFN|TO)[ -]?(\d+)\b',text,re.I)
        return f'{m.group(1).upper()}-{m.group(2)}' if m else None
    @staticmethod
    def _json_objects(soup):
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                d=json.loads(tag.string or tag.get_text()); items=d if isinstance(d,list) else [d]
                for x in items:
                    if isinstance(x,dict): yield x
            except Exception: pass
    def _jsonld(self,soup,key):
        for x in self._json_objects(soup):
            v=x.get(key)
            if isinstance(v,dict): v=v.get('name')
            if v:return clean_text(v)
    def _jsonld_price(self,soup):
        for x in self._json_objects(soup):
            o=x.get('offers'); o=o[0] if isinstance(o,list) and o else o
            if isinstance(o,dict) and o.get('price'): return parse_price(o['price'])
