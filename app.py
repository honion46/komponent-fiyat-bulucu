import streamlit as st
from src.core.query_parser import parse_query
from src.services.search_engine import SearchEngine

st.set_page_config(page_title='Komponent Fiyat Bulucu',page_icon='🔎',layout='wide')
st.title('🔎 Komponent Fiyat Bulucu')
st.caption('Gerçek satıcı sayfalarından ürün, stok ve fiyat bilgisi çekmeye çalışır.')
q=st.text_input('Komponent ara',placeholder='LM358 DIP-8 10 adet')
if st.button('Fiyatları Bul',type='primary') and q.strip():
    parsed=parse_query(q)
    st.write(f'**MPN:** {parsed.mpn}  |  **Paket:** {parsed.package or "Belirtilmedi"}  |  **Miktar:** {parsed.quantity}')
    with st.spinner('Satıcılar aranıyor...'):
        results=SearchEngine().search(parsed)
    if not results: st.warning('Sonuç bulunamadı.')
    else:
        rows=[]
        for r in results:
            rows.append({'Satıcı':r.seller,'Ürün':r.name,'MPN':r.mpn or '','Paket':r.package or '','Stok':r.stock_text or ('Var' if r.in_stock else ('Yok' if r.in_stock is False else 'Bilinmiyor')),'Birim Fiyat':f'{r.unit_price} {r.currency}' if r.unit_price is not None else '','Toplam':f'{r.total_price} {r.currency}' if r.total_price is not None else '','Güven':f'{r.confidence:.0%}','URL':r.url,'Hata':r.error or ''})
        st.dataframe(rows,use_container_width=True,hide_index=True)
else:
    st.info('Örnek: LM358 DIP-8 10 adet, MP1584, TB6612FNG 5 adet')
