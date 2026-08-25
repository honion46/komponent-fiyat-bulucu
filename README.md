# komponent-fiyat-bulucu

Kısa açıklama

Bu repo, bileşen fiyatlarını araştırma amaçlı araçları ve yardımcı kodları içerir. Yeni eklenen özellik: bir Streamlit tabanlı "Debug Snapshots Viewer" (app.py) — JSON formatındaki debug snapshot'ları yükleyip görüntülemek için hızlı bir arayüz sağlar.

How to run (Streamlit)

1. Python sanal ortamı oluşturun ve etkinleştirin

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# Windows (cmd)
.\.venv\Scripts\activate
```

2. Bağımlılıkları yükleyin

```bash
pip install -r requirements.txt
```

3. Streamlit uygulamasını çalıştırın

```bash
streamlit run app.py
```

4. Tarayıcıda açılan sayfadan JSON yükleyin

- Sidebar'dan "Fetch URL" ile bir endpoint verin (örn. `/api/debug_snapshots`) veya
- "Upload file" ile JSON dosyası yükleyin veya
- "Paste JSON" seçeneği ile doğrudan yapıştırın.

Notlar

- Daha önce repo'ya eklenmiş olan React tabanlı DebugSnapshotsViewer bileşeni artık cleanup ile kaldırıldı; aktif ve desteklenen yol Streamlit uygulamasıdır.
- Eğer tarayıcı tabanlı bir viewer isterseniz, React versiyonunu geri getirebilir veya yeni bir frontend entegrasyonu yapabilirim.

Destek ve test

- Uygulamayı yerelde çalıştırdıktan sonra herhangi bir sorun olursa `app.py` dosyasını ve `requirements.txt` içeriğini kontrol edin. Benim eklediğim örnekler minimal bağımlılıklarla çalışacak şekilde tasarlandı.

