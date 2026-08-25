import React, { useEffect, useState } from 'react';
import './DebugSnapshotsViewer.css';

type Snapshot = {
  id?: string | number;
  timestamp?: string;
  [key: string]: any;
};

type Props = {
  // Provide snapshots directly if you have them, otherwise set fetchUrl to load them
  snapshots?: Snapshot[];
  fetchUrl?: string; // e.g. '/api/debug_snapshots' returning JSON array
  autoFetch?: boolean; // default true when fetchUrl provided
};

export const DebugSnapshotsViewer: React.FC<Props> = ({ snapshots: initialSnapshots, fetchUrl, autoFetch = true }) => {
  const [snapshots, setSnapshots] = useState<Snapshot[]>(initialSnapshots ?? []);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [filterText, setFilterText] = useState<string>('');

  useEffect(() => {
    if (!initialSnapshots && fetchUrl && autoFetch) {
      setLoading(true);
      fetch(fetchUrl)
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => {
          if (Array.isArray(data)) setSnapshots(data as Snapshot[]);
          else if (data && data.snapshots && Array.isArray(data.snapshots)) setSnapshots(data.snapshots as Snapshot[]);
          else setError('Beklenmeyen veri yapısı — dizi bekleniyordu.');
        })
        .catch((e: any) => setError(String(e.message ?? e)))
        .finally(() => setLoading(false));
    }
  }, [initialSnapshots, fetchUrl, autoFetch]);

  useEffect(() => {
    if (snapshots.length && selectedIndex === null) setSelectedIndex(0);
  }, [snapshots, selectedIndex]);

  const filtered = snapshots.filter((s) => {
    if (!filterText) return true;
    try {
      return JSON.stringify(s).toLowerCase().includes(filterText.toLowerCase());
    } catch (e) {
      return false;
    }
  });

  const select = (i: number) => setSelectedIndex(i);

  return (
    <div className="dfv-root">
      <div className="dfv-sidebar">
        <div className="dfv-header">
          <h3>Debug Snapshots</h3>
          <input
            className="dfv-filter"
            placeholder="Ara (JSON içinde)"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            aria-label="Filter snapshots"
          />
        </div>

        {loading && <div className="dfv-note">Yükleniyor...</div>}
        {error && <div className="dfv-error">Hata: {error}</div>}

        <ul className="dfv-list">
          {filtered.map((s, i) => (
            <li
              key={(s && (s.id ?? s.timestamp)) ?? i}
              className={i === selectedIndex ? 'selected' : ''}
              onClick={() => select(i)}
            >
              <div className="dfv-item-title">{s.timestamp ?? `#${i}`}</div>
              <div className="dfv-item-sub">{Object.keys(s).length} keys</div>
            </li>
          ))}
          {filtered.length === 0 && <li className="dfv-empty">Eşleşen snapshot yok</li>}
        </ul>
      </div>

      <div className="dfv-main">
        {selectedIndex === null ? (
          <div className="dfv-placeholder">Bir snapshot seçin</div>
        ) : (
          (() => {
            const s = filtered[selectedIndex] ?? snapshots[selectedIndex];
            return (
              <div className="dfv-content">
                <div className="dfv-meta">
                  <strong>Snapshot</strong>
                  <span>{s?.timestamp ?? `#${selectedIndex}`}</span>
                </div>
                <pre className="dfv-json">{JSON.stringify(s, null, 2)}</pre>
              </div>
            );
          })()
        )}
      </div>
    </div>
  );
};

export default DebugSnapshotsViewer;
