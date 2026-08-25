import streamlit as st

from src.core.query_parser import parse_query
from src.services.search_engine import SearchEngine


st.set_page_config(
    page_title="Komponent Fiyat Bulucu",
    page_icon="🔎",
    layout="wide",
)

st.title("🔎 Komponent Fiyat Bulucu")
st.caption("Elektronik komponentleri farklı satıcılarda arayın ve karşılaştırın.")

query = st.text_input(
    "Komponent ara",
    placeholder="Örn: LM358 DIP-8 10 adet",
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
        except Exception as exc:
            st.error("Arama motorunda hata oluştu.")
            st.exception(exc)
            results = []

    if not results:
        st.warning("Sonuç bulunamadı.")

    else:
        rows = []

        for r in results:

            # Eski/yeni ProductResult sürümleriyle uyumlu
            seller = getattr(r, "seller", "")
            name = getattr(r, "name", "")
            mpn = getattr(r, "mpn", None)
            package = getattr(r, "package", None)

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

            unit_price = getattr(r, "unit_price", None)
            currency = getattr(r, "currency", "TRY")
            confidence = getattr(r, "confidence", 0.0)
            url = getattr(r, "url", "")
            error = getattr(r, "error", None)

            # total_price eski modellerde olmayabilir
            try:
                total_price = getattr(r, "total_price", None)
            except Exception:
                total_price = None

            rows.append(
                {
                    "Satıcı": seller,
                    "Ürün": name,
                    "MPN": mpn or "",
                    "Paket": package or "",
                    "Stok": stock,
                    "Birim Fiyat": (
                        f"{unit_price} {currency}"
                        if unit_price is not None
                        else ""
                    ),
                    "Toplam": (
                        f"{total_price} {currency}"
                        if total_price is not None
                        else ""
                    ),
                    "Güven": f"{confidence:.0%}",
                    "URL": url,
                    "Hata": error or "",
                }
            )

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )

else:
    st.info(
        "Örnekler: "
        "`MP1584`, "
        "`LM358 DIP-8 10 adet`, "
        "`TB6612FNG 5 adet`"
    )
