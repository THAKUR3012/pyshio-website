"""Background maintenance. Run every 5 minutes from a cPanel Cron Job (see DEPLOY_GODADDY.md):
    python jobs.py

It is safe to run more or less often; every step only acts on what is due. Each job lives in its slice's
jobs.py (features/<slice>/jobs.py); this file runs them in order:
  - releases unpaid booking / order holds
  - reminds pharmacies about orders waiting for acceptance, and auto-rejects (with refund) after the deadline
  - emails appointment reminders (~24 h before) and "prescription expiring" nudges
  - emails "refill due" reminders a few days before a long-term medicine course runs out
  - reminds labs about bookings they haven't accepted
  - reminds patients about follow-ups their doctor asked for, and sends opted-in "today's medicines" emails
  - reads uploaded prescription photos whose background read didn't finish
  - scores upcoming visits and sends one "Still coming?" to high no-show risks; weekly adherence-drop notes
  - checks pharma products for unusual prescribing changes (once a day; alerts once a week)
  - purges expired sessions, old login failures, reset links and rate-limit counters
"""
import logging
import sys

import features
from shared.db import init_db
from features.admin.jobs import purge_old_rows
from features.appointments.jobs import release_holds, send_due_reminders
from features.follow_ups.jobs import remind_follow_ups
from features.labs.jobs import chase_labs
from features.medicine_orders.jobs import chase_pharmacies
from features.medicine_reminders.jobs import dose_digest, dose_nudges
from features.notifications.jobs import purge_notifications
from features.pharma_nba.jobs import company_anomalies
from features.prescription_uploads.jobs import read_pending_uploads
from features.risk_nudges.jobs import adherence_alerts, nudge_risky_appointments
from features.prescriptions.jobs import nudge_expiring_prescriptions, remind_refills

features.load_all()  # every slice's hooks (e.g. in-app notifications for each email)

__all__ = ["release_holds", "purge_old_rows", "chase_pharmacies", "send_due_reminders", "nudge_expiring_prescriptions",
           "remind_refills", "chase_labs", "remind_follow_ups", "dose_digest", "dose_nudges", "purge_notifications", "read_pending_uploads", "nudge_risky_appointments",
           "adherence_alerts", "company_anomalies", "run_all", "main"]


def run_all() -> dict:
    release_holds()
    purge_old_rows()
    summary = chase_pharmacies()
    summary["appointment_reminders"] = send_due_reminders()
    summary["expiry_nudges"] = nudge_expiring_prescriptions()
    summary["refill_reminders"] = remind_refills()
    summary["lab_reminders"] = chase_labs()
    summary["follow_up_reminders"] = remind_follow_ups()
    summary["dose_digests"] = dose_digest()
    summary["dose_nudges"] = dose_nudges()
    summary["notifications_purged"] = purge_notifications()
    summary["uploads_read"] = read_pending_uploads()
    summary["still_coming_nudges"] = nudge_risky_appointments()
    summary["adherence_alerts"] = adherence_alerts()
    summary["company_alerts"] = company_anomalies()
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--test-email":
        import send_reminders
        return send_reminders.main(argv)
    init_db()
    print(run_all())
    return 0


if __name__ == "__main__":
    sys.exit(main())
