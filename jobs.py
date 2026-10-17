"""Pyshio Background Maintenance Jobs Launcher.

This root wrapper enables running `python jobs.py` directly from the project root.
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

# Load backend/jobs.py directly
backend_jobs_path = BACKEND_DIR / "jobs.py"
spec = importlib.util.spec_from_file_location("backend_jobs", backend_jobs_path)
backend_module = importlib.util.module_from_spec(spec)
sys.modules["backend_jobs"] = backend_module
spec.loader.exec_module(backend_module)

if __name__ == "__main__":
    sys.exit(backend_module.main())
