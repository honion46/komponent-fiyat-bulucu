
import time
import urllib.parse

import streamlit as st
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

st.set_page_config(page_title="Selenium Bağlantı Testi", page_icon="🧪")

st.title("🧪 Selenium / Streamlit Cloud Testi")
st.caption("Mevcut app.py'ye dokunmaz. Sadece Robotistan bağlantısını test eder.")

query = st.text_input("Test ürünü", value="L293D")

if st.button("Robotistan'ı Test Et", type="primary"):
    url = "https://www.robotistan.com/arama?q=" + urllib.parse.quote_plus(query)

    st.write("**Test URL:**", url)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=tr-TR")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    driver = None
    started = time.time()

    try:
        st.info("⏳ Chrome başlatılıyor...")

        binary_candidates = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ]

        for path in binary_candidates:
            import os
            if os.path.exists(path):
                options.binary_location = path
                st.write("Chrome binary:", path)
                break

        try:
            service = Service("/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as first_error:
            st.warning(
                "Sistem chromedriver ile başlatılamadı; webdriver-manager deneniyor."
            )
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)

        st.success("✅ Chrome başlatıldı")

        driver.set_page_load_timeout(30)
        driver.get(url)

        st.success("✅ Robotistan sayfası açıldı")

        time.sleep(3)

        title = driver.title
        current_url = driver.current_url
        html = driver.page_source
        body = driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        )

        st.write("### Sonuç")
        st.write("**Sayfa başlığı:**", title)
        st.write("**Açılan URL:**", current_url)
        st.write("**HTML uzunluğu:**", len(html))
        st.write("**Görünen metin uzunluğu:**", len(body))
        st.write("**L293D sayfada:**", "L293D" in body.upper())
        st.write("**TL fiyat ifadesi:**", "TL" in body.upper())
        st.write("**Geçen süre:**", f"{time.time() - started:.1f} saniye")

        st.write("### Sayfanın ilk 3000 karakteri")
        st.code(body[:3000])

        if len(html) < 5000:
            st.error("⚠️ HTML çok kısa. Sayfa engellenmiş/boş dönüyor olabilir.")
        elif "L293D" not in body.upper():
            st.warning(
                "⚠️ Sayfa geldi fakat L293D görünmüyor. "
                "Robotistan Cloud/Selenium isteğine farklı içerik döndürüyor olabilir."
            )
        else:
            st.success(
                "🎯 L293D sayfada bulundu. "
                "Bu durumda bağlantı çalışıyor; sorun ürün ayrıştırma kodunda aranmalı."
            )

        try:
            screenshot = driver.get_screenshot_as_png()
            st.image(screenshot, caption="Robotistan'ın Selenium ile aldığı ekran")
        except Exception as exc:
            st.caption(f"Ekran görüntüsü alınamadı: {type(exc).__name__}")

    except Exception as exc:
        st.error(f"❌ Selenium testi başarısız: {type(exc).__name__}")
        st.code(str(exc))

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
