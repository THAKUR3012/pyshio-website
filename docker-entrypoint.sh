#!/usr/bin/env bash
set -e

# Wait for PostgreSQL if configured
wait_for_db() {
    python - << 'EOF'
import sys
import time
from shared import db, settings

if db.is_postgres():
    print("Waiting for PostgreSQL connection...")
    for attempt in range(30):
        try:
            with db.get_db() as conn:
                conn.execute("SELECT 1;")
                print("Connected to PostgreSQL successfully.")
                sys.exit(0)
        except Exception as e:
            time.sleep(1)
    print("Error: Could not connect to PostgreSQL within 30 seconds.", file=sys.stderr)
    sys.exit(1)
else:
    print("Using SQLite database.")
EOF
}

init_application_db() {
    python - << 'EOF'
from shared.db import init_db
from shared import auth
from features.medicines import logic as medicines

print("Checking and initializing database...")
init_db()
auth.ensure_admin()
medicines.seed_catalogue()
print("Database initialization complete.")
EOF
}

# If the command is 'jobs', run background tasks on a 5-minute recurring loop
if [ "$1" = "jobs" ]; then
    wait_for_db
    echo "Starting Pyshio background maintenance worker (every 5 minutes)..."
    while true; do
        python jobs.py || echo "Warning: jobs.py encountered an issue, retrying in 5m..."
        sleep 300
    done
fi

# Default execution path (web server)
wait_for_db
init_application_db

exec "$@"
