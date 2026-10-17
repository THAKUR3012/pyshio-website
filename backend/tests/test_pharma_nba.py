"""MR next-best-action (privacy: anonymous doctors ranked on public facts only), visit briefs, company anomaly alerts."""
import pytest

from conftest import MONDAY, TUESDAY, next_weekday
from test_mr import many_signups, opt_in, pharma_world, write_rx  # noqa: F401  (fixtures)

pytestmark = pytest.mark.usefixtures("many_signups")


def add_competitor(platform):
    with platform.db.get_db() as conn:
        conn.execute("INSERT INTO medicines (name, generic_name, strength, form, pack_size, created_at, salt_key, strength_key, care_area) "
                     "VALUES ('Telsar 40', 'Telmisartan', '40 mg', 'tablet', 15, datetime('now'), 'telmisartan', '40mg', 'Cardiology')")


def test_named_doctor_ranked_on_share_gap_with_reasons(platform, pharma_world):
    w = pharma_world
    add_competitor(platform)
    write_rx(platform, w["sharing"], ["Telsar 40"], patients=6)
    plan = w["mr"].get("/api/mr/next-actions").json()
    top = plan["next"][0]
    assert top["name"] == "Dr. Sharing" and top["opportunity"]["share_percent"] == 0
    assert any("top competitor: Telsar 40" in r for r in top["reasons"])
    assert any("haven't met" in r for r in top["reasons"])


def test_anonymous_doctors_rank_never_depends_on_prescribing(platform, pharma_world):
    w = pharma_world
    add_competitor(platform)
    quiet = platform.gp(name="Dr. Quiet")
    write_rx(platform, w["private"], ["Telsar 40"], patients=6)        # Dr. Private prescribes a lot, Dr. Quiet nothing
    plan = w["mr"].get("/api/mr/next-actions").json()
    by_name = {d["name"]: d for d in plan["next"]}
    assert by_name["Dr. Private"]["score"] == by_name["Dr. Quiet"]["score"]
    assert "opportunity" not in by_name["Dr. Private"]
    assert not any("Prescribes" in r for r in by_name["Dr. Private"]["reasons"])
    assert quiet  # used


def test_open_requests_and_opted_out_doctors(platform, pharma_world):
    w = pharma_world
    opt_in(w["private"], share=False, meetings=False)
    res = w["mr"].post("/api/mr/requests", json={"doctor_id": w["sharing"].user["id"], "purpose": "Product update"})
    assert res.status_code == 200, res.text
    plan = w["mr"].get("/api/mr/next-actions").json()
    assert [d["name"] for d in plan["waiting"]] == ["Dr. Sharing"]
    assert "Dr. Private" not in [d["name"] for d in plan["next"]]


def test_visit_brief_builtin_and_claude(platform, pharma_world, fake_claude):
    w = pharma_world
    doctor_id = w["sharing"].user["id"]
    fake_claude.reply("- Discuss blood pressure control\n- Bring Telma 40 data\n- Ask about follow-up adherence")
    brief = w["mr"].get(f"/api/mr/next-actions/{doctor_id}/brief").json()
    assert brief["engine"] == "claude" and len(brief["bullets"]) == 3
    assert "Patient" not in fake_claude.sent_text()
    fake_claude.fail()
    assert w["mr"].get(f"/api/mr/next-actions/{doctor_id}/brief").json()["engine"] == "built-in"
    assert w["mr"].get("/api/mr/next-actions/999999/brief").status_code == 404


def test_company_drop_alert_once_a_week(platform, pharma_world):
    w = pharma_world
    wednesday = next_weekday(2)
    for day in (MONDAY, TUESDAY, wednesday):
        write_rx(platform, w["sharing"], ["Telma 40"], patients=7, day=day)
    with platform.db.get_db() as conn:   # all of that was 5-6 weeks ago; nothing since
        conn.execute("UPDATE prescriptions SET created_at = datetime('now', '-40 days')")
    import jobs
    platform.outbox.clear()
    assert jobs.company_anomalies() == 1 and jobs.company_anomalies() == 0      # once a day / once a week
    mail = platform.mails_to(w["company"].user["email"])[0]
    assert "Telma 40" in mail["text"]
    alerts = w["company"].get("/api/company/alerts").json()
    assert alerts[0]["type"] == "drop" and alerts[0]["recent"] == 0
    assert w["mr"].get("/api/mr/alerts").json()[0]["product"] == "Telma 40"
    with platform.db.get_db() as conn:  # company sign-ups have their own fixed hourly limit
        conn.execute("DELETE FROM rate_limits")
    other = platform.company("Other Pharma")
    assert other.get("/api/company/alerts").json() == []


def test_small_numbers_raise_no_alert(platform, pharma_world):
    w = pharma_world
    write_rx(platform, w["sharing"], ["Telma 40"], patients=3)
    with platform.db.get_db() as conn:
        conn.execute("UPDATE prescriptions SET created_at = datetime('now', '-40 days')")
    import jobs
    assert jobs.company_anomalies() == 0
    assert w["company"].get("/api/company/alerts").json() == []


@pytest.mark.parametrize("path", ["/api/mr/next-actions", "/api/mr/alerts"])
def test_only_mrs(platform, pharma_world, path):
    assert pharma_world["sharing"].get(path).status_code == 403
