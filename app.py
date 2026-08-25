import streamlit as st
import json
from typing import Any, List
from urllib.request import urlopen
from urllib.error import URLError

st.set_page_config(page_title="Debug Snapshots Viewer", layout="wide")

st.title("Debug Snapshots Viewer")

st.sidebar.header("Load snapshots")
load_mode = st.sidebar.radio("Source", ["Upload file", "Fetch URL", "Paste JSON", "Example data"], index=1)

# Example sample data
EXAMPLE_SNAPSHOTS = [
    {"id": 1, "timestamp": "2026-08-25T12:00:00Z", "user": "tester", "type": "info", "details": {"value": 42}},
    {"id": 2, "timestamp": "2026-08-25T12:01:00Z", "user": "tester", "type": "error", "details": {"message": "something went wrong"}},
]

snapshots: List[Any] = []
error = None

if load_mode == "Upload file":
    uploaded = st.sidebar.file_uploader("Upload JSON file with snapshots (array)", type=["json"])
    if uploaded is not None:
        try:
            data = json.load(uploaded)
            if isinstance(data, dict) and "snapshots" in data and isinstance(data["snapshots"], list):
                snapshots = data["snapshots"]
            elif isinstance(data, list):
                snapshots = data
            else:
                error = "Uploaded JSON must be an array of snapshots or an object with a 'snapshots' array."
        except Exception as e:
            error = f"Failed to parse JSON: {e}"

elif load_mode == "Fetch URL":
    url = st.sidebar.text_input("Snapshot JSON URL (must return JSON array)", value="/api/debug_snapshots")
    if st.sidebar.button("Fetch"):
        try:
            with urlopen(url) as r:
                raw = r.read()
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict) and "snapshots" in data and isinstance(data["snapshots"], list):
                    snapshots = data["snapshots"]
                elif isinstance(data, list):
                    snapshots = data
                else:
                    error = "Fetched JSON must be an array of snapshots or an object with a 'snapshots' array."
        except URLError as e:
            error = f"Network error: {e}"
        except Exception as e:
            error = f"Failed to fetch/parse JSON: {e}"

elif load_mode == "Paste JSON":
    txt = st.sidebar.text_area("Paste JSON here (array of snapshots)")
    if st.sidebar.button("Load pasted JSON"):
        try:
            data = json.loads(txt)
            if isinstance(data, dict) and "snapshots" in data and isinstance(data["snapshots"], list):
                snapshots = data["snapshots"]
            elif isinstance(data, list):
                snapshots = data
            else:
                error = "Pasted JSON must be an array of snapshots or an object with a 'snapshots' array."
        except Exception as e:
            error = f"Failed to parse JSON: {e}"

else:  # Example data
    if st.sidebar.button("Load example snapshots"):
        snapshots = EXAMPLE_SNAPSHOTS

if error:
    st.sidebar.error(error)

if not snapshots:
    st.info("No snapshots loaded. Use the sidebar to upload, fetch, paste, or load example snapshots.")
    st.stop()

# Filtering
filter_text = st.sidebar.text_input("Filter (search JSON)")

# Build filtered indices
def matches(s: Any, q: str) -> bool:
    if not q:
        return True
    try:
        return q.lower() in json.dumps(s).lower()
    except Exception:
        return False

filtered = [s for s in snapshots if matches(s, filter_text)]

st.sidebar.markdown(f"**Total:** {len(snapshots)}  \n**Shown:** {len(filtered)}")

# List and details layout
col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("Snapshots")
    options = []
    for i, s in enumerate(filtered):
        title = s.get("timestamp") if isinstance(s, dict) else None
        if not title:
            title = f"#{i}"
        summary = " — " + ", ".join(list(s.keys())[:3]) if isinstance(s, dict) else ""
        options.append(f"{title}{summary}")

    idx = None
    if filtered:
        idx = st.selectbox("Select snapshot", list(range(len(filtered))), format_func=lambda x: options[x])

with col2:
    st.subheader("Snapshot details")
    if filtered and idx is not None:
        sel = filtered[idx]
        st.write("Metadata:")
        if isinstance(sel, dict):
            meta = {k: v for k, v in sel.items() if k in ("id", "timestamp", "user", "type")}
            st.json(meta)
        st.write("Full JSON:")
        st.json(sel)

# Optionally allow download
if filtered:
    st.download_button("Download shown snapshots as JSON", data=json.dumps(filtered, indent=2), file_name="debug_snapshots_filtered.json", mime="application/json")
