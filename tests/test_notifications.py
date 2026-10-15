"""In-app notifications: every email also lands in the bell; secret-link emails never do."""
from conftest import ADMIN_EMAIL


def bell(client):
    res = client.get("/api/notifications")
    assert res.status_code == 200, res.text
    return res.json()


def fresh(client):
    """New accounts start with a welcome note; clear it so tests count only what follows."""
    for item in bell(client)["items"]:
        client.delete(f"/api/notifications/{item['id']}")
    return client


def test_booking_lands_in_both_bells(platform):
    doctor = fresh(platform.doctor())
    patient = platform.patient()
    welcome = bell(patient)["items"]
    assert [i["title"] for i in welcome] == ["Welcome to Pyshio"] and welcome[0]["link"] == "/account#profile"
    fresh(patient)
    platform.book(patient, doctor)
    mine = bell(patient)
    assert mine["unread"] == 1
    item = mine["items"][0]
    assert item["title"].startswith("Your appointment is confirmed") and item["link"] == "/account"
    assert item["kind"] == "appointment" and not item["read"] and item["body"]
    theirs = bell(doctor)["items"]
    assert theirs[0]["title"].startswith("New appointment booked") and theirs[0]["link"] == "/doctor"


def test_demo_addresses_still_get_updates_and_reset_links_never_stored(platform):
    walk_in = fresh(platform.patient(email="walkin@pyshio-demo.invalid"))
    doctor = platform.doctor()
    platform.book(walk_in, doctor)
    assert bell(walk_in)["unread"] == 1  # no email possible, but the bell has it
    before = bell(walk_in)["unread"]
    assert platform.client().post("/api/auth/forgot", json={"email": "walkin@pyshio-demo.invalid"}).status_code == 200
    assert bell(walk_in)["unread"] == before
    assert all("reset" not in (i["link"] or "") for i in bell(walk_in)["items"])


def test_read_mark_all_delete_and_privacy(platform):
    doctor = platform.doctor()
    patient = fresh(platform.patient())
    platform.book(patient, doctor, time="10:00")
    platform.book(patient, doctor, time="11:00")
    items = bell(patient)["items"]
    assert len(items) == 2
    other = fresh(platform.patient(name="Someone Else"))
    # Nobody else can read, mark or delete them
    assert bell(other)["items"] == []
    assert other.post("/api/notifications/read", json={"ids": [items[0]["id"]]}).json()["marked"] == 0
    assert other.delete(f"/api/notifications/{items[0]['id']}").status_code == 404
    res = patient.post("/api/notifications/read", json={"ids": [items[0]["id"]]}).json()
    assert res == {"marked": 1, "unread": 1}
    assert patient.post("/api/notifications/read", json={"all": True}).json() == {"marked": 1, "unread": 0}
    assert patient.delete(f"/api/notifications/{items[1]['id']}").status_code == 200
    assert len(bell(patient)["items"]) == 1
    assert platform.client().get("/api/notifications").status_code == 401


def test_admin_hears_about_new_doctors(platform):
    platform.doctor(approve=False)
    admin = platform.admin()
    titles = [i["title"] for i in bell(admin)["items"]]
    assert any("approval needed" in t.lower() for t in titles), titles
    assert ADMIN_EMAIL


def test_repeated_reminder_is_not_duplicated(platform):
    patient = fresh(platform.patient())
    n = platform.notifications
    from shared.db import get_db
    with get_db() as conn:
        assert n.add(conn, patient.user["id"], "Time for your night medicines", "Dolo 650", "/account#today")
        assert n.add(conn, patient.user["id"], "Time for your night medicines", "Dolo 650", "/account#today") is None
    assert bell(patient)["unread"] == 1


def test_old_read_notifications_are_purged(platform, monkeypatch):
    from datetime import timedelta
    doctor = platform.doctor()
    patient = platform.patient()
    platform.book(patient, doctor)
    patient.post("/api/notifications/read", json={"all": True})
    real = platform.settings.now()
    monkeypatch.setattr(platform.settings, "now", lambda: real + timedelta(days=91))
    import jobs
    assert jobs.purge_notifications() >= 1
