# Streamlit Debug Snapshots Viewer

This PR adds a Streamlit-based viewer to quickly inspect debug snapshot JSON data inline with the repository.

Files added/modified on branch add-debug-snapshots-ui:
- app.py (Streamlit UI to load/upload/fetch/paste snapshots and inspect them)
- requirements.txt (added requests)

Usage:
- Run locally: pip install -r requirements.txt && streamlit run app.py
- In the Streamlit UI you can fetch from an endpoint (e.g. /api/debug_snapshots), upload a JSON file, or paste JSON.

Notes:
- The repository previously contained a React component under src/components/ for a browser-based viewer; because this repo appears to be a Python/Streamlit app, this Streamlit integration lives in app.py so you can run it directly.
- If you prefer a different integration (e.g., add a dedicated module or a route), tell me and I can update accordingly.
