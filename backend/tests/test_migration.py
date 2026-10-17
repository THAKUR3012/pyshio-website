"""Upgrading an existing database keeps every account and record and fills in the new fields."""
import sqlite3


def test_upgrade_from_version_3_keeps_data(platform, monkeypatch, tmp_path):
    db = platform.db
    path = tmp_path / "old.db"
    monkeypatch.setattr(platform.settings, "DB_PATH", str(path))
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:3])
    db.init_db()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO users (id, role, name, email, password_hash, created_at, dob) "
                 "VALUES (1, 'doctor', 'Dr Old', 'old@example.com', 'x', '2025-01-01', '1980-05-05')")
    conn.execute("INSERT INTO users (id, role, name, email, password_hash, created_at) "
                 "VALUES (2, 'patient', 'Pat Old', 'pat@example.com', 'x', '2025-01-01')")
    conn.execute("INSERT INTO doctors (user_id, slug, qualification, registration_no, clinic_name, address, city, "
                 "created_at, status) VALUES (1, 'dr-old', 'MBBS', 'R1', 'Clinic', 'Road', 'Mumbai', '2025-01-01', 'approved')")
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES ('t', 2, '2025', '2099')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    conn.close()

    monkeypatch.undo()  # back to every migration, still on the copied file
    monkeypatch.setattr(platform.settings, "DB_PATH", str(path))
    db.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    users = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM users")}
    assert users[1]["email"] == "old@example.com" and users[1]["dob"] == "1980-05-05"
    assert users[2]["role"] == "patient" and "state" in users[2]
    assert conn.execute("SELECT state FROM doctors WHERE user_id = 1").fetchone()[0] == "Maharashtra"  # back-filled
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM conditions").fetchone()[0] >= 100
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # New roles are accepted
    conn.execute("INSERT INTO users (role, name, email, password_hash, created_at) VALUES ('mr', 'Rep', 'r@x.com', 'x', '2026')")
    conn.close()
