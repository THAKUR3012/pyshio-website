"""Build dist/pyshio-godaddy.zip containing only what the server needs.

Deliberately excludes the local database, .env (passwords), tests, legacy files and caches.
"""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = [
    # entry points (everything else lives in shared/, features/ and web/)
    "server.py", "passenger_wsgi.py", "jobs.py", "send_reminders.py",
    # config & docs
    "requirements.txt", ".env.example", "DEPLOY_GODADDY.md",
]
FOLDERS = ["shared", "features", "web"]
SKIP = ("__pycache__", ".pyc")


def main():
    out = ROOT / "dist" / "pyshio-godaddy.zip"
    out.parent.mkdir(exist_ok=True)
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            zf.write(ROOT / name, name)
            names.append(name)
        for folder in FOLDERS:
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and not any(x in path.as_posix() for x in SKIP):
                    rel = path.relative_to(ROOT).as_posix()
                    zf.write(path, rel)
                    names.append(rel)
    print(f"Created {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB, {len(names)} files)")


if __name__ == "__main__":
    main()
