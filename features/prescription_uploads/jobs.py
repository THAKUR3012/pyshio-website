"""Prescription uploads: scheduled work (run by jobs.py every 5 minutes)."""
from features.prescription_uploads import reader


def read_pending_uploads() -> int:
    """Read uploaded prescription photos whose background read didn't finish (up to 5 per run)."""
    return reader.read_pending()
