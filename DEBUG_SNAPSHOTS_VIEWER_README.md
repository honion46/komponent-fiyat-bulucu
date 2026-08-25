# Debug Snapshots Viewer

Bu dalde (add-debug-snapshots-ui) React + TypeScript için basit bir DebugSnapshotsViewer komponenti eklendi. Amaç: backend tarafındaki debug_snapshots verilerini hızlıca görebilmek için küçük bir UI sunmak.

Dosyalar eklendi:
- src/components/DebugSnapshotsViewer.tsx
- src/components/DebugSnapshotsViewer.css

Özellikler:
- snapshots prop'u ile doğrudan JSON verisi geçilebilir.
- fetchUrl prop'u verildiğinde (örn. `/api/debug_snapshots`) komponent otomatik olarak veriyi çeker.
- Basit filtreleme, listeden seçim ve JSON biçimlendirilmiş görüntüleme.

Kullanım örneği (React uygulamanıza aşağıdaki gibi ekleyin):

```tsx
import React from 'react'
import DebugSnapshotsViewer from './components/DebugSnapshotsViewer'

// Eğer backend endpoint varsa:
// <DebugSnapshotsViewer fetchUrl="/api/debug_snapshots" />

// Veya doğrudan snapshot dizisi verin:
// <DebugSnapshotsViewer snapshots={mySnapshotsArray} />
```

Notlar:
- Bu değişiklik sadece frontend komponentini ekler; backend tarafında `/api/debug_snapshots` gibi bir endpoint yoksa fetchUrl ile otomatik yükleme hata verir — bunun yerine snapshots prop'u kullanın.
- İsterseniz benzer bir sayfa/route (ör. /debug-snapshots) için örnek bir sayfa dosyası da ekleyebilirim (Next.js/React Router projenize göre uyarlama gerekir).

Sonraki adımlar önerim:
1) Eğer isterseniz PR açabilirim (başlık: "Add debug snapshots viewer UI", açıklama: "Adds a simple UI to view debug_snapshots for easier debugging. Scaffolds a viewer component, route, and basic styles; no backend changes.").
2) Ya da repository yapısına göre bir route sayfası ekleyip doğrudan entegre edebilirim — hangi router/SSG çerçevesini kullandığınızı belirtin (Next.js, CRA/React Router, Vite, vs.).
