import os

DEBUG_DIR = "debug_snapshots"
os.makedirs(DEBUG_DIR, exist_ok=True)

def scrape_site(site: str, url_tmpl: str, query: str):
    encoded_query = urllib.parse.quote_plus(query)
    url = url_tmpl.format(query=encoded_query)
    base_url = "https://" + url.split("://", 1)[1].split("/", 1)[0]

    driver = None
    try:
        driver = get_driver()
        driver.set_page_load_timeout(20)
        driver.get(url)

        if site in ["Direnc.net", "Samm Market", "Motorobit"]:
            time.sleep(5.0)
        elif site in ["Hepsiburada", "Trendyol", "N11", "Amazon TR"]:
            time.sleep(4.0)
        else:
            time.sleep(1.5)

        html = driver.page_source
        products = extract_products(html, base_url, site, query)

        # DEBUG: 0 sonuç varsa HTML + screenshot kaydet
        if not products:
            safe_name = site.replace(" ", "_").replace(".", "")
            with open(f"{DEBUG_DIR}/{safe_name}.html", "w", encoding="utf-8") as f:
                f.write(html)
            driver.save_screenshot(f"{DEBUG_DIR}/{safe_name}.png")

        status = f"{len(products)} ürün bulundu" if products else "Ürün bulunamadı"
        return site, products, status
    except Exception as e:
        return site, [], f"Bağlantı Hatası / Engellendi: {e}"
    finally:
        if driver:
            driver.quit()
