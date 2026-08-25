import streamlit as st
from src.core.query_parser import parse_query
from src.services.search_engine import SearchEngine

st.set_page_config(page_title='Komponent Fiyat Bulucu', page_icon='🔎', layout='wide')
st.title('🔎 Komponent Fiyat Bulucu')
st.caption('Komponent sorgusunu ayrıştırır ve satıcı sonuçlarını karşılaştırmaya hazırlar.')
query = st.text_input('Komponent / BOM ara', placeholder='Örn: LM358 DIP-8 10 adet')
if st.button('Fiyatları Bul', type='primary') and query.strip():
    parsed = parse_query(query)
    st.subheader('Ayrıştırılan sorgu')
    st.json(parsed.to_dict())
    results = SearchEngine().search(parsed)
    if results:
        st.dataframe([r.to_dict() for r in results], use_container_width=True, hide_index=True)
    else:
        st.warning('Sonuç bulunamadı.')
