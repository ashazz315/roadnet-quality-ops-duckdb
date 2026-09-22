"""Production entry point: explicit persistent storage and editor access required."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from roadinsight_ui.workspace import configuration

if __name__ == "__main__":
    os.environ["ROADINSIGHT_MODE"] = "server"
    configuration()
    port = int(os.environ.get("PORT", "8501"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", str(ROOT / "roadinsight_app.py"),
             "--server.address", "0.0.0.0", "--server.port", str(port), "--server.headless", "true",
             "--server.enableXsrfProtection", "true", "--server.enableCORS", "true",
             "--server.maxUploadSize", "20", "--server.maxMessageSize", "50",
             "--server.fileWatcherType", "none", "--browser.gatherUsageStats", "false"])
