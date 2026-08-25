"""
Streamlit UI: Price Finder (extended with Turkey adapters)

This version pre-fills URL templates and selectors for several Turkish suppliers (Robotistan, RobotHobi, Hepsiburada, N11).
These selectors are placeholders and may need correction per site. The UI allows editing the URL template and selector before lookup.
"""
import streamlit as st
import pandas as pd
from typing import List
from price_finder import MockAdapter, ScraperAdapter, lookup_prices
from turkiye_adapters import DEFAULTS

st.set_page_config(page_title="Price Finder", layout="wide")
st.title("Price Finder — Komponent Fiyat Bulucu")

st.sidebar.header("Input SKUs")
input_mode = st.sidebar.radio("Input mode", ["Paste list", "Upload CSV"], index=0)

skus: List[str] = []
if input_mode == "Paste list":
    txt = st.sidebar.text_area("Paste SKUs (one per line)", height=150, placeholder=" e.g.\nABC-123\nCDE-456")
    if txt:
        skus = [line.strip() for line in txt.splitlines() if line.strip()]
else:
    uploaded = st.sidebar.file_uploader("Upload CSV (one SKU per line or with header 'sku')", type=["csv"])
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            if "sku" in df.columns:
                skus = df["sku"].astype(str).tolist()
            else:
                # assume first column
                skus = df.iloc[:,0].astype(str).tolist()
        except Exception as e:
            st.sidebar.error(f"Failed to parse CSV: {e}")

st.sidebar.markdown("---")
st.sidebar.header("Adapter")
adapter_choice = st.sidebar.selectbox("Adapter", ["Mock", "Scraper", "Robotistan", "RobotHobi", "Hepsiburada", "N11"], index=0)

adapter = None

if adapter_choice == "Mock":
    adapter = MockAdapter(seed=1)
    st.sidebar.info("Mock adapter returns deterministic example prices for testing.")

elif adapter_choice == "Scraper":
    st.sidebar.markdown("Scraper configuration")
    url_template = st.sidebar.text_input("URL template (use {sku})", value="https://example.com/search?q={sku}")
    price_selector = st.sidebar.text_input("Price CSS selector (e.g. .price)", value=".price")
    st.sidebar.markdown("If you don't know the selector, use Mock adapter first. Scraping real sites may be blocked or require cookies/JS.")
    adapter = ScraperAdapter(url_template=url_template, price_css_selector=price_selector)

else:
    # Turkey-specific presets
    preset = adapter_choice
    defaults = DEFAULTS.get(preset, {})
    st.sidebar.markdown(f"{preset} preset (defaults provided; edit before lookup if needed)")
    url_template = st.sidebar.text_input("URL template (use {sku})", value=defaults.get("url_template", "https://example.com/search?q={sku}"))
    price_selector = st.sidebar.text_input("Price CSS selector (e.g. .price)", value=defaults.get("price_selector", ".price"))
    st.sidebar.markdown("Note: These presets use simple HTML scraping. Many sites use JS rendering or anti-bot measures; in that case, use API adaptors or provide a working selector.")
    adapter = ScraperAdapter(url_template=url_template, price_css_selector=price_selector)

if st.sidebar.button("Lookup prices"):
    if not skus:
        st.sidebar.error("No SKUs provided.")
    else:
        with st.spinner("Looking up prices..."):
            results = lookup_prices(adapter, skus)
        df = pd.DataFrame(results)
        # Normalize price column for sorting/filtering
        df["price_float"] = pd.to_numeric(df["price"], errors="coerce")
        st.success(f"Done — {len(df)} items")
        # Filters
        colf1, colf2, colf3 = st.columns([1,1,1])
        min_price = colf1.number_input("Min price", value=float(pd.Series(df["price_float"].dropna()).min() if df["price_float"].notna().any() else 0.0))
        max_price = colf2.number_input("Max price (0 = no limit)", value=0.0)
        in_stock_only = colf3.checkbox("In stock only", value=False)
        # Apply filters
        qdf = df.copy()
        qdf = qdf[(qdf["price_float"].isna()) | (qdf["price_float"] >= min_price)]
        if max_price > 0:
            qdf = qdf[(qdf["price_float"].isna()) | (qdf["price_float"] <= max_price)]
        if in_stock_only:
            qdf = qdf[qdf["in_stock"] == True]
        # Show table
        show_cols = ["supplier","sku","price","price_float","in_stock","url","error"]
        st.dataframe(qdf[show_cols].rename(columns={"price_float":"price_numeric"}), use_container_width=True)
        # Download CSV
        csv_buf = qdf.to_csv(index=False)
        st.download_button("Download results CSV", data=csv_buf, file_name="price_results.csv", mime="text/csv")
        # Quick summary
        st.markdown("### Quick summary")
        if qdf["price_float"].notna().any():
            best = qdf.loc[qdf["price_float"].idxmin()]
            st.write(f"Lowest price: {best['price']}  — SKU: {best['sku']}  — Supplier: {best['supplier']}  — Link: {best.get('url')}")
        else:
            st.write("No numeric prices found.")
else:
    st.info("Provide SKUs and click 'Lookup prices' in the sidebar.")
