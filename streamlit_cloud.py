"""Community Cloud entry point; public browsing and optional protected editing."""
import os
import runpy
import tempfile
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

# Never expose the unrestricted local mode through the hosted entry point.
os.environ["ROADINSIGHT_MODE"] = "community"
os.environ.setdefault("ROADINSIGHT_DATA_DIR", str(Path(tempfile.gettempdir()) / "roadinsight-community"))
try:
    configured_token = st.secrets.get("ROADINSIGHT_EDITOR_TOKEN", os.environ.get("ROADINSIGHT_EDITOR_TOKEN", ""))
except StreamlitSecretNotFoundError:
    configured_token = os.environ.get("ROADINSIGHT_EDITOR_TOKEN", "")
os.environ["ROADINSIGHT_EDITOR_TOKEN"] = str(configured_token)
runpy.run_path(str(Path(__file__).with_name("roadinsight_app.py")), run_name="__main__")
