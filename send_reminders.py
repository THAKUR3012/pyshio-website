"""Email patients a reminder about 24 hours before their appointment (the job itself lives in
features/appointments/jobs.py; older cron lines still call this script).

    python send_reminders.py                          # runs the full maintenance job (reminders included)
    python send_reminders.py --test-email you@example.com   # check your SMTP settings
"""
import argparse
import logging
import sys

import features
from shared import mailer
from features.appointments.jobs import REMINDER_WINDOW, send_due_reminders, send_test_email

features.load_all()

__all__ = ["REMINDER_WINDOW", "send_due_reminders", "send_test_email", "main"]


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-email", metavar="ADDRESS", help="send a sample email and exit")
    args = parser.parse_args(argv)

    if args.test_email:
        if not mailer.is_configured():
            print("SMTP_HOST is not set; nothing sent. See DEPLOY_GODADDY.md for email settings.")
            return 1
        ok = send_test_email(args.test_email)
        print("Test email sent." if ok else "Test email FAILED; see the error above.")
        return 0 if ok else 1
    import jobs
    return jobs.main([])


if __name__ == "__main__":
    sys.exit(main())
