"""After the patient chooses: the others are told and stop getting the request; a patient <-> partner chat opens."""
import pytest

from test_network import prescribe, world  # noqa: F401  (fixture)
from test_uploads import PNG, quote, upload


@pytest.fixture
def booked(platform, world):
    """An uploaded prescription quoted by two pharmacies; the patient books the near one."""
    w = world
    w["up"] = upload(w["patient"]).json()
    near_req = w["near"].get("/api/partner/uploads").json()[0]
    mid_req = w["mid"].get("/api/partner/uploads").json()[0]
    quote(w["near"], near_req["id"], [{"name": "Dolo 650", "quantity": 10, "unit_price": 2}])
    quote(w["mid"], mid_req["id"], [{"name": "Dolo 650", "quantity": 10, "unit_price": 2.5}])
    q = w["patient"].get(f"/api/my/uploads/{w['up']['id']}").json()["medicines"]["options"][0]
    platform.outbox.clear()
    w["order"] = w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders", json={"quote_id": q["id"], "fulfilment": "pickup"}).json()
    return w


def test_others_are_told_the_patient_chose_someone_else(platform, booked):
    w = booked
    assert any("patient chose another pharmacy" in m["subject"] for m in platform.mails_to(w["mid"].user["email"]))
    assert not any("chose another" in m["subject"] for m in platform.mails_to(w["near"].user["email"]))
    assert w["mid"].get("/api/partner/uploads").json() == []
    mid = w["mid"].get("/api/partner/outcomes").json()
    assert mid["lost"] == 1 and mid["outcomes"][0]["outcome"] == "chosen_other" and mid["outcomes"][0]["reference"] == w["up"]["reference"]
    assert w["near"].get("/api/partner/outcomes").json()["won"] == 1
    # If the patient cancels, the request reopens and the "lost" mark goes away
    w["patient"].post(f"/api/my/upload-orders/{w['order']['id']}/cancel", json={})
    assert w["mid"].get("/api/partner/uploads").json() and w["mid"].get("/api/partner/outcomes").json()["lost"] == 0


def test_chat_opens_by_default_and_either_side_can_close(platform, booked):
    w = booked
    chat_id = w["order"]["chat_id"]
    assert chat_id
    mine = w["patient"].get("/api/chats").json()["chats"]
    assert [c["id"] for c in mine] == [chat_id] and mine[0]["with_name"] == "Near Pharmacy"
    thread = w["near"].get(f"/api/chats/{chat_id}").json()
    assert thread["with_name"] == "Asha Rao" and thread["messages"][0]["author_role"] == "system"

    w["patient"].post(f"/api/chats/{chat_id}/messages", json={"body": "Is Dolo 650 in stock? I'll come at 6 PM."})
    assert w["near"].get("/api/chats").json()["unread"] == 1
    reply = w["near"].post(f"/api/chats/{chat_id}/messages", json={"body": "Yes, packed and waiting for you."}).json()
    assert [m["author_role"] for m in reply["messages"]][-2:] == ["patient", "pharmacy"]
    assert w["near"].get("/api/chats").json()["unread"] == 0 and w["patient"].get("/api/chats").json()["unread"] == 1

    # Order updates appear in the chat
    w["near"].post(f"/api/partner/upload-orders/{w['order']['id']}/status", json={"status": "accepted"})
    assert "accepted" in w["patient"].get(f"/api/chats/{chat_id}").json()["messages"][-1]["body"]

    # Nobody else can read it
    for other in (w["mid"], w["gp"], platform.patient(name="Someone Else")):
        assert other.get(f"/api/chats/{chat_id}").status_code in (403, 404)
    assert platform.admin().get(f"/api/chats/{chat_id}").status_code == 403

    # Close, can't write; reopen, can write again
    closed = w["near"].post(f"/api/chats/{chat_id}/close").json()
    assert closed["status"] == "closed" and "closed this chat" in closed["messages"][-1]["body"]
    assert w["patient"].post(f"/api/chats/{chat_id}/messages", json={"body": "Hello?"}).status_code == 400
    assert w["patient"].post(f"/api/chats/{chat_id}/reopen").json()["status"] == "open"
    assert w["patient"].post(f"/api/chats/{chat_id}/messages", json={"body": "Thanks!"}).status_code == 200


def test_doctor_prescription_orders_and_lab_bookings_also_open_chats(platform, world):
    w = world
    platform.stock(w["near"], "Dolo 650", price=15)
    dolo = platform.medicine(w["gp"], "Dolo 650")
    rx = prescribe(platform, w, items=[{"medicine_id": dolo["id"], "quantity": 10}])
    # Mid pharmacy quotes, the patient orders from near pharmacy's price list instead
    req = w["mid"].get("/api/pharmacy/requests").json()[0]
    item = req["prescription"]["items"][0]
    res = w["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/quote",
                        json={"items": [{"prescription_item_id": item["id"], "available": True, "unit_price": 3}]})
    assert res.status_code == 200, res.text
    platform.outbox.clear()
    order = w["patient"].post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": w["near"].user["id"]}).json()
    assert order["chat_id"]
    assert any("chose another pharmacy" in m["subject"] for m in platform.mails_to(w["mid"].user["email"]))
    assert w["mid"].get("/api/partner/outcomes").json()["lost"] == 1

    lab = platform.lab("Chat Labs", km_from_home=0.6)
    platform.lab_price(lab, "Complete Blood Count (CBC)", 300)
    cbc = [t for t in w["gp"].get("/api/lab-tests?q=Complete").json()][0]
    rx2 = w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions", json={"tests": [{"test_id": cbc["id"]}]}).json()
    booking = w["patient"].post(f"/api/prescriptions/{rx2['id']}/lab-bookings", json={"lab_id": lab.user["id"]}).json()
    assert booking["chat_id"]
    titles = [c["title"] for c in w["patient"].get("/api/chats").json()["chats"]]
    assert len(titles) == 2 and any("Chat Labs" in t for t in titles)
