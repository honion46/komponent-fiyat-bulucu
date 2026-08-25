import streamlit as st
from src.core.query_parser import parse_query
from src.services.search_engine import SearchEngine

st.set_page_config(
    page_title="Komponent Fiyat Bulucu",
    page_icon="🔎",
    layout="wide"
)

st.title("🔎 Komponent Fiyat Bulucu")
st.caption("Elektronik komponentleri farklı satıcılarda arayın ve karşılaştırın.")

query = st.text_input(
    "Komponent ara",
    placeholder="Örn: LM358 DIP-8 10 adet"
)

if st.button("Fiyatları Bul", type="primary") and query.strip():

    parsed = parse_query(query)

    st.markdown(
        f"**MPN:** {parsed.mpn} | "
        f"**Paket:** {parsed.package or 'Belirtilmedi'} | "
        f"**Miktar:** {parsed.quantity}"
    )

    with st.spinner("Satıcılar aranıyor..."):
        try:
            results = SearchEngine().search(parsed)
        except Exception as e:
            st.error(f"Arama motorunda hata oluştu: {e}")
            st.exception(e)
            results = []

    if not results:
        st.warning("Sonuç bulunamadı.")
    else:

        rows = []

        for r in results:

            unit_price = getattr(r, "unit_price", None)
            total_price = getattr(r, "total_price", None)

            stock_text = getattr(r, "stock_text", None)
            in_stock = getattr(r, "in_stock", None)

            if stock_text:
                stock = stock_text
            elif in_stock is True:
                stock = "Var"
            elif in_stock is False:
                stock = "Yok"
            else:
                stock = "Bilinmiyor"

            confidence = getattr(r, "confidence", 0.0)

            rows.append({
                "Satıcı": getattr(r, "seller", ""),
                "Ürün": getattr(r, "name", ""),
                "MPN": getattr(r, "mpn", "") or "",
                "Paket": getattr(r, "package", "") or "",
                "Stok": stock,
                "Birim Fiyat": str(unit_price) if unit_price is not None else "",
                "Toplam": str(total_price) if total_price is not None else "",
                "Güven": f"{confidence:.0%}",
                "URL": getattr(r, "url", "") or "",
                "Hata": getattr(r, "error", "") or "",
            })

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True
        )

else:
    st.info(
        "Örnek aramalar: "
        "`LM358 DIP-8 10 adet`, "
        "`MP1584`, "
        "`TB6612FNG 5 adet`"
    )
