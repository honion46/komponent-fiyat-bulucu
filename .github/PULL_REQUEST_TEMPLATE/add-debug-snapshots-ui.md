---
title: "Add debug snapshots viewer UI"
body: |
  Adds a simple UI to view debug_snapshots for easier debugging. Scaffolds a viewer component and basic styles; no backend changes.

  Files added:
  - src/components/DebugSnapshotsViewer.tsx
  - src/components/DebugSnapshotsViewer.css
  - DEBUG_SNAPSHOTS_VIEWER_README.md

  Usage:
  - Use <DebugSnapshotsViewer fetchUrl="/api/debug_snapshots" /> if you have an endpoint, or pass snapshots prop directly.

  Notes:
  - This PR only adds frontend components. Add a route or page to integrate into the app.

labels: []
assignees: []
---
