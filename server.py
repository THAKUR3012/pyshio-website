"""Pyshio Web App Launcher.

This root wrapper enables running `python server.py` directly from the project root
while executing the backend application inside `backend/`.
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.chdir(BACKEND_DIR)

# Load backend/server.py directly
backend_server_path = BACKEND_DIR / "server.py"
spec = importlib.util.spec_from_file_location("backend_server", backend_server_path)
backend_module = importlib.util.module_from_spec(spec)
sys.modules["backend_server"] = backend_module
spec.loader.exec_module(backend_module)

app = backend_module.app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
