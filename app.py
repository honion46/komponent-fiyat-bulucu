# Robotzade için site-özel parser -- ekle
def parse_robotzade(html: str, base_url: str, site: str, query: str, relevance_threshold: float = 0.5) -> List[Product]:
    """
    Robotzade sayfalarında 'KDV Dahil Fiyatı' gibi açık etiketleri hedefler.
    Bulamazsa element yakınlarında PRICE_RE arar, yine bulamazsa generic fallback uygular.
    """
    soup = BeautifulSoup(html, "lxml")
    keywords = [k.lower() for k in query.split() if len(k) > 1]
    results: List[Product] = []

    # 1) Düz metinde açık ifadeyi ara: "KDV Dahil Fiyatı : 41.16 TL"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"kdv\s*dahil\s*fiyat[iı]?\s*[:\-]?\s*([\d\.,\s]+)\s*(?:TL|₺|TRY)?", text, re.IGNORECASE)
    if m:
        price_val = parse_price(m.group(1))
        # Deney: sayfa başlığından isim al
        title_tag = soup.find(["h1", "h2", "title"])
        name = title_tag.get_text(" ", strip=True) if title_tag else query
        # Ürün sayfas olduğundan link base_url'yi kullan; eğer canonical varsa onu al
        canonical = None
        can_tag = soup.find("link", rel="canonical")
        if can_tag and can_tag.get("href"):
            canonical = url_join(base_url, can_tag["href"])
        else:
            # fallback: base_url (istersen link'i tam ürün url'si yap)
            canonical = base_url
        # Relevance filtre uygula
        if is_relevant(name, keywords, relevance_threshold):
            results.append(Product(site=site, name=name, price=price_val, url=canonical))
            return results

    # 2) Eğer açık ifade yoksa: "kdv" içeren elementleri tara ve parent içinde fiyat ara
    texts_with_kdv = soup.find_all(string=re.compile(r"kdv", re.IGNORECASE))
    for txt in texts_with_kdv:
        parent = txt.parent
        # yukarı doğru birkaç seviye kontrol et
        for _ in range(4):
            if parent is None:
                break
            block_text = parent.get_text(" ", strip=True)
            m2 = PRICE_RE.search(block_text)
            if m2:
                price_val = parse_price(m2.group(1))
                # ürün başlığını bulmaya çalış
                h = soup.find(["h1", "h2"])
                name = h.get_text(" ", strip=True) if h else txt.strip()
                link = base_url
                if is_relevant(name, keywords, relevance_threshold):
                    results.append(Product(site=site, name=name, price=price_val, url=link))
                    return results
            parent = parent.parent

    # 3) Element bazlı arama: fiyat sınıfı olan elementleri tara, label "KDV" yakınsa al
    price_candidates = []
    for selector in [".price", ".product-price", ".prd-price", ".price-new", ".amount", ".prc"]:
        for el in soup.select(selector):
            txt = el.get_text(" ", strip=True)
            m3 = PRICE_RE.search(txt)
            if m3:
                # parent'te 'kdv' ifadesi var mı kontrol et
                parent_text = ""
                if el.parent:
                    parent_text = el.parent.get_text(" ", strip=True)
                if re.search(r"kdv", parent_text, re.IGNORECASE) or re.search(r"kdv", txt, re.IGNORECASE):
                    price_candidates.append((el, parse_price(m3.group(1))))
    if price_candidates:
        el, price_val = price_candidates[0]
        h = soup.find(["h1", "h2"])
        name = h.get_text(" ", strip=True) if h else (el.get("aria-label") or query)
        link = base_url
        if is_relevant(name, keywords, relevance_threshold):
            results.append(Product(site=site, name=name, price=price_val, url=link))
            return results

    # 4) Son çare: generic parser ile devam et
    logger.debug("parse_robotzade: site-özel tespit yok, generic fallback uygulanıyor")
    return parse_generic(html, base_url, site, query, relevance_threshold=relevance_threshold)
