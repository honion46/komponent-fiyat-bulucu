# Komponent Fiyat Bulucu V2

Gerçek satıcı adaptörleri eklenebilecek temiz çekirdek: sorgu ayrıştırma, MPN/paket/miktar çıkarımı, fiyat normalizasyonu, ürün eşleştirme ve Streamlit arayüzü.

Şimdilik sahte fiyat üretilmez. Yapılandırılmamış satıcılar açıkça veri alınamadığını bildirir.

```bash
pip install -r requirements.txt
streamlit run app.py
pytest -q
```
