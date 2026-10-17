"""Fill the database with FICTIONAL demo data, mostly around Mumbai, for testing.

    python seed_demo.py            # add demo data (safe to re-run: existing demo data is replaced)
    python seed_demo.py --wipe     # remove every demo account and everything linked to it

Everything is clearly fake:
  - names/stores/companies end in "(Demo)", registration & licence numbers start with DEMO-
  - emails end in @pyshio-demo.invalid, so no email is ever sent to them
  - all demo logins use the password  demo-pass-123

What you get:
  - doctors and pharmacies across 19 Mumbai areas, plus a few in Pune, Bengaluru, Delhi, Ahmedabad,
    Hyderabad and Chennai (so state/city analytics have something to compare)
  - 5 patients to log in as, plus ~450 background patients with 6 months of visits, revisits, coded
    diagnoses, prescriptions, refills and completed orders (feeds history, forecasts and analytics)
  - 3 pharma companies with divisions, their own demo brands, 12 MRs and meeting requests;
    about a third of doctors are shown by name in pharma insights (the rest anonymously)
  - 30 labs with price lists; doctors order tests, patients book labs, labs upload (fake) PDF reports with
    key results, so health records and trends (e.g. HbA1c over time) have data; patients' other medicines

Never run this on the live site. It is deliberately left out of the GoDaddy upload package.
"""
from datetime import datetime, timedelta
import math
import random
import sys
import uuid

from pathlib import Path
import shutil

from shared import auth, settings, reference_data as ref
from shared.common import new_reference, stamp
from shared.db import get_db, init_db, write_txn
from features.medicine_courses import logic as medicine_courses
from features.medicines import logic as medicines
from features.prescriptions import logic as prescriptions
from features.medicine_reminders import logic as medicine_reminders
from features.video_consults.logic import new_room

DEMO_DOMAIN = "pyshio-demo.invalid"
PASSWORD = "demo-pass-123"
rng = random.Random(2026)  # same data every run

# Real Mumbai neighbourhoods (approximate centres); demo addresses are jittered around them
AREAS = [
    ("Bandra West", 19.0596, 72.8295, "400050"), ("Khar West", 19.0710, 72.8360, "400052"),
    ("Santacruz West", 19.0810, 72.8380, "400054"), ("Vile Parle East", 19.0990, 72.8500, "400057"),
    ("Juhu", 19.1075, 72.8263, "400049"), ("Andheri West", 19.1364, 72.8296, "400058"),
    ("Andheri East", 19.1136, 72.8697, "400069"), ("Powai", 19.1176, 72.9060, "400076"),
    ("Goregaon West", 19.1663, 72.8490, "400062"), ("Malad West", 19.1874, 72.8484, "400064"),
    ("Borivali West", 19.2307, 72.8567, "400092"), ("Kurla West", 19.0726, 72.8845, "400070"),
    ("Ghatkopar East", 19.0860, 72.9081, "400077"), ("Chembur", 19.0522, 72.9005, "400071"),
    ("Dadar West", 19.0178, 72.8420, "400028"), ("Matunga", 19.0270, 72.8570, "400019"),
    ("Worli", 19.0176, 72.8162, "400018"), ("Lower Parel", 18.9986, 72.8300, "400013"),
    ("Colaba", 18.9067, 72.8147, "400005"),
]
# A few other cities (city, state, lat, lng, pin, council) so analytics can compare states
OTHER_CITIES = [
    ("Pune", "Maharashtra", 18.5204, 73.8567, "411004", "Maharashtra Medical Council"),
    ("Bengaluru", "Karnataka", 12.9716, 77.5946, "560001", "Karnataka Medical Council"),
    ("Delhi", "Delhi", 28.6315, 77.2167, "110001", "Delhi Medical Council"),
    ("Ahmedabad", "Gujarat", 23.0225, 72.5714, "380009", "Gujarat Medical Council"),
    ("Hyderabad", "Telangana", 17.3850, 78.4867, "500001", "Telangana State Medical Council"),
    ("Chennai", "Tamil Nadu", 13.0827, 80.2707, "600006", "Tamil Nadu Medical Council"),
]
STREETS = ["SV Road", "Linking Road", "LBS Marg", "Hill Road", "Station Road", "MG Road", "Link Road",
           "Ambedkar Road", "Tilak Road", "Turner Road", "Carter Road", "Veera Desai Road", "Hiranandani Gardens"]
FIRST = ["Aarav", "Ananya", "Rohan", "Isha", "Vikram", "Priya", "Kabir", "Sneha", "Arjun", "Meera", "Nikhil", "Pooja",
         "Rahul", "Kavya", "Siddharth", "Neha", "Aditya", "Riya", "Karan", "Tanvi", "Varun", "Shruti", "Amit", "Divya",
         "Sameer", "Aditi", "Manish", "Nisha", "Harsh", "Swati"]
FEMALE = {"Ananya", "Isha", "Priya", "Sneha", "Meera", "Pooja", "Kavya", "Neha", "Riya", "Tanvi", "Shruti", "Divya",
          "Aditi", "Nisha", "Swati"}
LAST = ["Shah", "Mehta", "Iyer", "Desai", "Kulkarni", "Patil", "Joshi", "Rao", "Nair", "Kapoor", "Menon", "Pillai",
        "Bhatt", "Sawant", "Gokhale", "Fernandes", "D'Souza", "Khan", "Sheikh", "Chavan"]
STORE_WORDS = ["Sai", "Shree", "Om", "Lifeline", "Wellness", "Apna", "Care", "Health Plus", "Jai", "Sanjeevani",
               "Arogya", "MedPoint", "City", "Family", "Trust", "Green Cross", "New", "Guardian"]

# (category, qualification, council, specialities pool, fee range, how many)
DOCTOR_MIX = [
    ("physiotherapist", "BPT, MPT", "Maharashtra State OT & PT Council", 20, (400, 1200)),
    ("general_physician", "MBBS", "Maharashtra Medical Council", 15, (300, 800)),
    ("specialist", "MBBS, MD", "Maharashtra Medical Council", 12, (800, 2000)),
    ("dentist", "BDS", "Maharashtra State Dental Council", 5, (400, 1000)),
    ("ayush", "BAMS", "Maharashtra Council of Indian Medicine", 3, (300, 700)),
]
EXTRA_SERVICES = {
    "physiotherapist": [("Initial assessment", 60, 1.5), ("Follow-up session", 30, 0.8), ("Home visit", 45, 2.0)],
    "general_physician": [("Follow-up consultation", 15, 0.6)],
    "specialist": [("Follow-up consultation", 20, 0.7)],
    "dentist": [("Scaling & cleaning", 45, 1.8)],
    "ayush": [("Panchakarma consultation", 45, 1.5)],
}
# Printed MRP per pack (₹) for the starter catalogue, so discounts and "% off MRP" can be tested
DEMO_MRP = {
    "Dolo 650": 33.6, "Calpol 650": 30.9, "Crocin 500": 22.0, "Calpol 500": 16.4, "Brufen 400": 18.2, "Pan 40": 155.0,
    "Pantocid 40": 162.0, "Omez 20": 63.5, "Azithral 500": 119.5, "Azee 500": 71.1, "Cetzine 10": 19.6, "Okacet 10": 18.4,
    "Allegra 120": 239.0, "Glycomet 500": 31.0, "Telma 40": 128.0, "Amlong 5": 48.5, "Stamlo 5": 52.0, "Atorva 10": 98.0,
    "Lipicure 10": 59.0, "Ecosprin 75": 5.8, "Thyronorm 50": 169.0, "Eltroxin 50": 158.0, "Ondem 4": 52.5,
    "Amaryl 1": 150.0, "Januvia 100": 290.0, "Galvus 50": 230.0, "Rosuvas 10": 210.0, "Met XL 25": 95.0,
    "Clopilet 75": 85.0, "Montair 10": 230.0, "Levocet 5": 55.0, "Asthalin Inhaler": 160.0,
    "Foracort 200 Rotacap": 420.0, "Augmentin 625 Duo": 220.0, "Taxim-O 200": 110.0, "Doxy-1 100": 95.0,
    "Niftran 100": 140.0, "Urimax 0.4": 260.0, "Nexito 10": 190.0, "Serta 50": 120.0, "Lyrica 75": 340.0,
    "Hifenac 100": 85.0, "Voveran 50": 30.0, "Domstal 10": 38.0, "Razo 20": 140.0, "Udiliv 300": 640.0,
    "Uprise D3 60K": 120.0, "Methycobal 500": 120.0, "Folvite 5": 48.0, "Orofer XT": 165.0, "Terbicip 250": 180.0,
    "Moxicip Eye Drops": 95.0, "Refresh Tears": 200.0, "Hexidine Mouthwash": 110.0,
}

# ---- Pharma companies, divisions, their own (fictional) brands ----
COMPANIES = [
    ("Sunrise Pharma (Demo)", "Mumbai", [("Cardio-Diabetes", ["Cardiology", "Diabetes & Endocrine"]),
                                         ("Respiratory", ["Respiratory"])]),
    ("Vardaan Lifesciences (Demo)", "Pune", [("Pain & Neuro", ["Pain & Ortho", "Neurology"]),
                                             ("Gastro", ["Gastro & Liver"])]),
    ("Nirmal Healthcare (Demo)", "Ahmedabad", [("Anti-infectives", ["Infections", "ENT & Eye"]),
                                               ("Derma & Women", ["Dermatology", "Women's health"])]),
]
# (company index, name, generic, strength, form, pack, MRP ₹)
DEMO_BRANDS = [
    (0, "Telmisun 40 (Demo)", "Telmisartan", "40 mg", "tablet", 15, 98.0),
    (0, "Metsun 500 (Demo)", "Metformin", "500 mg", "tablet", 20, 26.0),
    (0, "Glimsun 1 (Demo)", "Glimepiride", "1 mg", "tablet", 30, 120.0),
    (0, "Rosusun 10 (Demo)", "Rosuvastatin", "10 mg", "tablet", 15, 160.0),
    (0, "Montesun 10 (Demo)", "Montelukast", "10 mg", "tablet", 15, 175.0),
    (1, "Aceva 100 (Demo)", "Aceclofenac", "100 mg", "tablet", 10, 68.0),
    (1, "Pregava 75 (Demo)", "Pregabalin", "75 mg", "capsule", 14, 260.0),
    (1, "Panvar 40 (Demo)", "Pantoprazole", "40 mg", "tablet", 15, 118.0),
    (1, "Rabevar 20 (Demo)", "Rabeprazole", "20 mg", "tablet", 15, 112.0),
    (2, "Azinir 500 (Demo)", "Azithromycin", "500 mg", "tablet", 3, 64.0),
    (2, "Cefinir 200 (Demo)", "Cefixime", "200 mg", "tablet", 10, 92.0),
    (2, "Terbinir 250 (Demo)", "Terbinafine", "250 mg", "tablet", 7, 150.0),
    (2, "Folinir 5 (Demo)", "Folic acid", "5 mg", "tablet", 45, 39.0),
]

# What doctors prescribe for each condition: [(brand choices, dosage, quantity, long-term)]
LONG_TERM = {"I10", "E11.9", "E78.5", "I25.1", "E03.9", "J45.9", "J44.9", "K76.0", "G62.9", "F41.1", "F32.9", "N40"}
TREATMENT = {
    "I10": [(["Telma 40", "Telmisun 40 (Demo)", "Amlong 5", "Stamlo 5"], "1-0-0", 30)],
    "E11.9": [(["Glycomet 500", "Metsun 500 (Demo)"], "1-0-1", 60), (["Amaryl 1", "Glimsun 1 (Demo)", "Januvia 100"], "1-0-0", 30)],
    "E78.5": [(["Atorva 10", "Rosuvas 10", "Rosusun 10 (Demo)", "Lipicure 10"], "0-0-1", 30)],
    "I25.1": [(["Ecosprin 75"], "0-1-0", 30), (["Clopilet 75"], "0-1-0", 30), (["Met XL 25"], "1-0-0", 30)],
    "E03.9": [(["Thyronorm 50", "Eltroxin 50"], "1-0-0", 30)],
    "J45.9": [(["Foracort 200 Rotacap"], "1-0-1", 60), (["Montair 10", "Montesun 10 (Demo)"], "0-0-1", 30)],
    "J44.9": [(["Foracort 200 Rotacap"], "1-0-1", 60), (["Asthalin Inhaler"], "SOS", 1)],
    "K76.0": [(["Udiliv 300"], "1-0-1", 60)],
    "G62.9": [(["Lyrica 75", "Pregava 75 (Demo)"], "0-0-1", 30), (["Methycobal 500"], "1-0-0", 30)],
    "F41.1": [(["Nexito 10", "Serta 50"], "0-0-1", 30)],
    "F32.9": [(["Nexito 10", "Serta 50"], "0-0-1", 30)],
    "N40": [(["Urimax 0.4"], "0-0-1", 30)],
    "J06.9": [(["Dolo 650", "Calpol 650", "Crocin 500"], "1-1-1", 10), (["Cetzine 10", "Levocet 5", "Okacet 10"], "0-0-1", 5)],
    "J30.4": [(["Allegra 120", "Levocet 5"], "0-0-1", 10), (["Montair 10", "Montesun 10 (Demo)"], "0-0-1", 10)],
    "R05": [(["Levocet 5", "Cetzine 10"], "0-0-1", 5)],
    "B34.9": [(["Dolo 650", "Calpol 650"], "1-1-1", 15)],
    "R50.9": [(["Dolo 650", "Calpol 650", "Crocin 500"], "1-1-1", 10)],
    "A90": [(["Dolo 650", "Calpol 650"], "1-1-1", 15)],
    "B54": [(["Dolo 650"], "1-1-1", 15)],
    "A92.0": [(["Dolo 650"], "1-1-1", 15), (["Hifenac 100", "Aceva 100 (Demo)"], "1-0-1", 10)],
    "A01.0": [(["Taxim-O 200", "Cefinir 200 (Demo)"], "1-0-1", 14), (["Dolo 650"], "1-1-1", 10)],
    "A09": [(["Ondem 4"], "1-0-1", 6), (["Pan 40", "Panvar 40 (Demo)"], "1-0-0", 5)],
    "K21.9": [(["Pan 40", "Pantocid 40", "Panvar 40 (Demo)", "Razo 20", "Rabevar 20 (Demo)"], "1-0-0", 15)],
    "K29.7": [(["Pan 40", "Razo 20", "Rabevar 20 (Demo)"], "1-0-0", 14), (["Domstal 10"], "1-0-1", 10)],
    "K30": [(["Pan 40", "Panvar 40 (Demo)"], "1-0-0", 10)],
    "M54.5": [(["Hifenac 100", "Aceva 100 (Demo)", "Voveran 50"], "1-0-1", 10), (["Pan 40", "Panvar 40 (Demo)"], "1-0-0", 5)],
    "M54.2": [(["Hifenac 100", "Aceva 100 (Demo)"], "1-0-1", 10)],
    "M17.9": [(["Hifenac 100", "Aceva 100 (Demo)"], "1-0-1", 20), (["Uprise D3 60K"], "Once a week", 4)],
    "M81.0": [(["Uprise D3 60K"], "Once a week", 8)],
    "M79.1": [(["Brufen 400", "Voveran 50"], "1-0-1", 10)],
    "G43.9": [(["Brufen 400", "Dolo 650"], "SOS", 10)],
    "L70.0": [(["Doxy-1 100"], "0-0-1", 30)],
    "B35.4": [(["Terbicip 250", "Terbinir 250 (Demo)"], "1-0-0", 14)],
    "N39.0": [(["Niftran 100"], "1-0-1", 10)],
    "E28.2": [(["Glycomet 500", "Metsun 500 (Demo)"], "1-0-1", 60), (["Folvite 5", "Folinir 5 (Demo)"], "1-0-0", 30)],
    "D50.9": [(["Orofer XT"], "1-0-0", 30)],
    "J02.9": [(["Azithral 500", "Azee 500", "Azinir 500 (Demo)"], "1-0-0", 3), (["Dolo 650"], "1-1-1", 10)],
    "H66.9": [(["Augmentin 625 Duo"], "1-0-1", 10), (["Dolo 650"], "1-1-1", 10)],
    "H10.9": [(["Moxicip Eye Drops"], "1 drop 4 times a day", 1)],
    "B01.9": [(["Dolo 650", "Calpol 500"], "1-1-1", 10)],
    "Z00.1": [(["Uprise D3 60K"], "Once a week", 4)],
    "T78.4": [(["Allegra 120", "Levocet 5"], "0-0-1", 10)],
    "K05.1": [(["Hexidine Mouthwash"], "Twice daily", 1)],
}
# Who gets what: conditions by age band, with weights (F/M-only conditions marked)
BY_AGE = {
    "child": [("J06.9", 6), ("R50.9", 4), ("A09", 3), ("H66.9", 2), ("B01.9", 1), ("Z00.1", 2), ("J02.9", 2)],
    "young": [("J06.9", 5), ("B34.9", 3), ("L70.0", 3), ("K21.9", 3), ("J30.4", 3), ("M54.5", 2), ("F41.1", 2),
              ("E28.2:F", 3), ("N39.0:F", 2), ("T78.4", 1), ("B35.4", 2), ("G43.9", 2), ("A90", 1)],
    "middle": [("I10", 5), ("E11.9", 5), ("K21.9", 4), ("M54.5", 4), ("E03.9:F", 3), ("E78.5", 3), ("J30.4", 2),
               ("F41.1", 2), ("J06.9", 3), ("K76.0", 2), ("D50.9:F", 2), ("B34.9", 2), ("G43.9", 1)],
    "older": [("I10", 7), ("E11.9", 6), ("E78.5", 5), ("M17.9", 4), ("I25.1", 3), ("J44.9", 2), ("N40:M", 3),
              ("M81.0:F", 3), ("G62.9", 2), ("K21.9", 2), ("J45.9", 2), ("E03.9:F", 2)],
}
MONSOON = [("A90", 4), ("B54", 3), ("B34.9", 4), ("A01.0", 2), ("A92.0", 2), ("A09", 2)]


def jitter(lat, lng, km=1.6):
    return round(lat + rng.uniform(-km, km) / 111.0, 6), round(lng + rng.uniform(-km, km) / 105.0, 6)


def person(i):
    return FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]


def demo_user_ids(conn):
    return [r[0] for r in conn.execute("SELECT id FROM users WHERE email LIKE ?", (f"%@{DEMO_DOMAIN}",))]


def wipe() -> int:
    """Delete every demo account and all rows that reference it."""
    with get_db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        with write_txn(conn):
            ids = demo_user_ids(conn)
            if not ids:
                return 0
            marks = ",".join("?" * len(ids))
            appts = f"SELECT id FROM appointments WHERE doctor_id IN ({marks}) OR patient_id IN ({marks})"
            rxs = f"SELECT id FROM prescriptions WHERE doctor_id IN ({marks}) OR patient_id IN ({marks})"
            orders = f"SELECT id FROM medicine_orders WHERE pharmacy_id IN ({marks}) OR patient_id IN ({marks}) " \
                     f"OR prescription_id IN ({rxs})"
            companies = f"SELECT id FROM companies WHERE admin_id IN ({marks})"
            two, four = (*ids, *ids), (*ids, *ids, *ids, *ids)
            conn.execute(f"DELETE FROM reviews WHERE patient_id IN ({marks}) OR target_id IN ({marks})", two)
            conn.execute(f"DELETE FROM messages WHERE appointment_id IN ({appts})", two)
            conn.execute(f"DELETE FROM courses WHERE order_id IN ({orders})", four)
            conn.execute(f"DELETE FROM order_items WHERE order_id IN ({orders})", four)
            conn.execute(f"DELETE FROM medicine_orders WHERE id IN ({orders})", four)
            conn.execute(f"DELETE FROM quote_items WHERE quote_id IN (SELECT id FROM quotes WHERE pharmacy_id IN ({marks}) "
                         f"OR prescription_id IN ({rxs}))", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM quotes WHERE pharmacy_id IN ({marks}) OR prescription_id IN ({rxs})", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM rx_requests WHERE pharmacy_id IN ({marks}) OR prescription_id IN ({rxs})", (*ids, *ids, *ids))
            convs = f"SELECT id FROM conversations WHERE patient_id IN ({marks}) OR partner_id IN ({marks})"
            conn.execute(f"DELETE FROM conversation_messages WHERE conversation_id IN ({convs})", two)
            conn.execute(f"DELETE FROM conversations WHERE id IN ({convs})", two)
            uploads_q = f"SELECT id FROM rx_uploads WHERE patient_id IN ({marks})"
            conn.execute(f"DELETE FROM upload_quote_lines WHERE quote_id IN (SELECT id FROM upload_quotes WHERE partner_id IN ({marks}) "
                         f"OR upload_id IN ({uploads_q}))", two)
            conn.execute(f"DELETE FROM upload_orders WHERE partner_id IN ({marks}) OR upload_id IN ({uploads_q})", two)
            conn.execute(f"DELETE FROM upload_quotes WHERE partner_id IN ({marks}) OR upload_id IN ({uploads_q})", two)
            conn.execute(f"DELETE FROM upload_requests WHERE partner_id IN ({marks}) OR upload_id IN ({uploads_q})", two)
            conn.execute(f"DELETE FROM rx_uploads WHERE patient_id IN ({marks})", ids)
            bookings = f"SELECT id FROM lab_bookings WHERE lab_id IN ({marks}) OR patient_id IN ({marks}) " \
                       f"OR prescription_id IN ({rxs})"
            conn.execute(f"DELETE FROM record_values WHERE record_id IN (SELECT id FROM health_records WHERE patient_id IN "
                         f"({marks}) OR lab_id IN ({marks}) OR lab_booking_id IN ({bookings}))", (*ids, *ids, *ids, *ids, *ids, *ids))
            conn.execute(f"DELETE FROM health_records WHERE patient_id IN ({marks}) OR lab_id IN ({marks}) "
                         f"OR lab_booking_id IN ({bookings})", (*ids, *ids, *ids, *ids, *ids, *ids))
            conn.execute(f"DELETE FROM lab_booking_items WHERE booking_id IN ({bookings})", four)
            conn.execute(f"DELETE FROM lab_bookings WHERE id IN ({bookings})", four)
            lab_quotes = f"SELECT id FROM lab_quotes WHERE lab_id IN ({marks}) OR prescription_id IN ({rxs})"
            conn.execute(f"DELETE FROM lab_quote_items WHERE quote_id IN ({lab_quotes})", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM lab_quotes WHERE id IN ({lab_quotes})", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM lab_requests WHERE lab_id IN ({marks}) OR prescription_id IN ({rxs})", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM prescription_tests WHERE prescription_id IN ({rxs})", two)
            conn.execute(f"DELETE FROM patient_medicines WHERE patient_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM notifications WHERE user_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM dose_logs WHERE patient_id IN ({marks})", ids)
            conn.execute(f"UPDATE prescriptions SET family_member_id = NULL WHERE id IN ({rxs})", two)
            conn.execute(f"UPDATE health_records SET family_member_id = NULL WHERE patient_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM prescription_conditions WHERE prescription_id IN ({rxs})", two)
            conn.execute(f"DELETE FROM prescription_items WHERE prescription_id IN ({rxs})", two)
            conn.execute(f"DELETE FROM prescriptions WHERE id IN ({rxs})", two)
            conn.execute(f"DELETE FROM appointments WHERE id IN ({appts})", two)
            conn.execute(f"DELETE FROM family_members WHERE patient_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM mr_requests WHERE mr_id IN ({marks}) OR doctor_id IN ({marks}) "
                         f"OR company_id IN ({companies})", (*ids, *ids, *ids))
            conn.execute(f"DELETE FROM mrs WHERE user_id IN ({marks}) OR company_id IN ({companies})", two)
            # Demo brands: drop them (and any reference from real data), un-claim anything else demo companies claimed
            demo_meds = f"SELECT id FROM medicines WHERE created_by IN ({marks})"
            conn.execute(f"DELETE FROM pharmacy_stock WHERE medicine_id IN ({demo_meds})", ids)
            for table in ("prescription_items", "order_items", "courses"):
                conn.execute(f"UPDATE {table} SET medicine_id = NULL WHERE medicine_id IN ({demo_meds})", ids)
            conn.execute(f"DELETE FROM medicines WHERE id IN ({demo_meds})", ids)
            conn.execute(f"UPDATE medicines SET company_id = NULL, manufacturer = NULL WHERE company_id IN ({companies})", ids)
            conn.execute(f"DELETE FROM divisions WHERE company_id IN ({companies})", ids)
            conn.execute(f"DELETE FROM companies WHERE admin_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM audit_log WHERE actor_id IN ({marks})", ids)
            for table, col in [("blocked_times", "doctor_id"), ("services", "doctor_id"), ("doctor_hours", "doctor_id"),
                               ("pharmacy_stock", "pharmacy_id"), ("payouts", "payee_id"), ("doctors", "user_id"),
                               ("lab_prices", "lab_id"), ("labs", "user_id"),
                               ("pharmacies", "user_id"), ("sessions", "user_id"), ("password_resets", "user_id")]:
                conn.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
        conn.execute("PRAGMA foreign_keys = ON")
    shutil.rmtree(Path(settings.UPLOAD_DIR) / "demo", ignore_errors=True)
    return len(ids)


def seed(doctors_n: int = 55, pharmacies_n: int = 55, patients_n: int = 5, history_patients: int = 450) -> dict:
    init_db()
    medicines.seed_catalogue()
    wipe()
    pw_hash = auth.hash_password(PASSWORD)  # one hash for every demo login (fast)
    now = stamp()
    with get_db() as conn:
        with write_txn(conn):
            def add_user(role, name, email, phone, **extra):
                cols = {"role": role, "name": name, "email": email, "phone": phone, "password_hash": pw_hash,
                        "created_at": now, **extra}
                cur = conn.execute(f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                   tuple(cols.values()))
                return cur.lastrowid

            # ---- Pharma companies & their brands (brands first: pharmacies stock them) ----
            company_ids, divisions = [], []
            for c, (cname, ccity, divs) in enumerate(COMPANIES, 1):
                admin = add_user("company", f"{person(c + 300)[0]} {person(c + 300)[1]}", f"company{c}@{DEMO_DOMAIN}",
                                 f"96{rng.randint(10000000, 99999999)}")
                cur = conn.execute("INSERT INTO companies (admin_id, name, cin, gstin, address, city, state, status, "
                                   "created_at, approved_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?, ?, 'approved', ?, ?, ?)",
                                   (admin, cname, f"DEMO-CIN-{c:04d}", f"Demo House, {ccity}", ccity,
                                    ref.state_for_city(ccity), now, now, now))
                company_ids.append((cur.lastrowid, admin, cname))
                for dname, areas in divs:
                    d = conn.execute("INSERT INTO divisions (company_id, name, care_areas, created_at) VALUES (?, ?, ?, ?)",
                                     (cur.lastrowid, dname, "|".join(areas), now))
                    divisions.append((cur.lastrowid, d.lastrowid, areas))
            for idx, name, generic, strength, form, pack, mrp in DEMO_BRANDS:
                company_id, admin, cname = company_ids[idx]
                conn.execute("INSERT INTO medicines (name, generic_name, strength, form, pack_size, rx_required, created_by, "
                             "created_at, mrp_paise, care_area, manufacturer, company_id) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                             (name, generic, strength, form, pack, admin, now, int(mrp * 100), ref.care_area_for(generic),
                              cname, company_id))
            # Catalogue MRPs (only where missing, never overriding real data)
            for name, mrp in DEMO_MRP.items():
                conn.execute("UPDATE medicines SET mrp_paise = ? WHERE name = ? AND mrp_paise IS NULL", (int(mrp * 100), name))
            catalogue = conn.execute("SELECT id, name, pack_size, mrp_paise FROM medicines WHERE mrp_paise IS NOT NULL").fetchall()

            # ---- Doctors ----
            n = 0
            mix = []
            for category, qual, council, count, fees in DOCTOR_MIX:
                mix += [(category, qual, council, fees)] * count
            mix = (mix * ((doctors_n // len(mix)) + 1))[:doctors_n]
            places = [(area, alat, alng, pin, "Mumbai", "Maharashtra", None) for area, alat, alng, pin in AREAS]
            doctor_rows = []

            def add_doctor(category, qual, council, fees, place):
                nonlocal n
                n += 1
                first, last = person(n)
                area, alat, alng, pin, city, state, own_council = place
                lat, lng = jitter(alat, alng)
                pool = settings.DOCTOR_CATEGORIES[category]["specialities"]
                specs = rng.sample(pool, k=min(len(pool), rng.randint(1, 3)))
                fee = rng.randrange(fees[0], fees[1] + 1, 50)
                uid = add_user("doctor", f"Dr. {first} {last} (Demo)", f"doctor{n:02d}@{DEMO_DOMAIN}",
                               f"98{rng.randint(10000000, 99999999)}")
                # About a third of doctors who prescribe share insights with MRs
                shares = category in ("general_physician", "specialist") and rng.random() < 0.45
                conn.execute(
                    """INSERT INTO doctors (user_id, slug, qualification, registration_no, registration_council,
                       experience_years, specialities, bio, clinic_name, address, city, pincode, slot_minutes,
                       accepts_clinic_payment, accepts_online_payment, status, created_at, approved_at, updated_at,
                       category, lat, lng, state, share_insights_at, accept_mr_meetings)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'approved', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uid, f"demo-dr-{first}-{last}-{n}".lower().replace("'", ""), qual, f"DEMO-{n:04d}",
                     own_council or council, rng.randint(2, 25), "|".join(specs),
                     f"Demo profile for testing only. {', '.join(specs)} in {area}.",
                     f"{last} {'Physio' if category == 'physiotherapist' else 'Clinic'} {area} (Demo)",
                     f"{rng.randint(1, 120)}, {rng.choice(STREETS)}, {area}", city, pin, rng.choice([15, 20, 30]),
                     int(rng.random() < 0.5), now, now, now, category, lat, lng, state, now if shares else None,
                     int(rng.random() < 0.85)))
                if category in ("physiotherapist", "general_physician") and rng.random() < 0.5:
                    conn.execute("UPDATE doctors SET offers_home_visits = 1, home_visit_radius_km = ?, home_visit_fee = ? WHERE user_id = ?",
                                 (rng.choice([3, 4, 5, 6]), rng.choice([150, 200, 250, 300, 400]), uid))
                hours = [(d, "10:00", "13:00") for d in range(6)] + [(d, "17:00", "20:00") for d in range(5)]
                if rng.random() < 0.3:
                    hours.append((5, "17:00", "19:00"))
                conn.executemany("INSERT INTO doctor_hours (doctor_id, weekday, start_time, end_time) VALUES (?, ?, ?, ?)",
                                 [(uid, d, s, e) for d, s, e in hours])
                cur = conn.execute("INSERT INTO services (doctor_id, name, duration, fee) VALUES (?, 'Consultation', 30, ?)",
                                   (uid, fee))
                doctor_rows.append({"id": uid, "category": category, "lat": lat, "lng": lng, "city": city, "state": state,
                                    "service_id": cur.lastrowid, "fee": fee})
                for name, minutes, factor in EXTRA_SERVICES[category]:
                    if rng.random() < 0.7:
                        conn.execute("INSERT INTO services (doctor_id, name, duration, fee) VALUES (?, ?, ?, ?)",
                                     (uid, name, minutes, int(round(fee * factor / 50) * 50)))

            for category, qual, council, fees in mix:
                add_doctor(category, qual, council, fees, places[(n + 1) % len(places)])
            for city, state, clat, clng, pin, council in OTHER_CITIES:
                for category, qual, fees in (("general_physician", "MBBS", (300, 700)), ("specialist", "MBBS, MD", (700, 1500))):
                    add_doctor(category, qual, council, fees, (city, clat, clng, pin, city, state, council))

            # ---- Pharmacies ----
            pharmacy_rows = []

            def add_pharmacy(i, place):
                area, alat, alng, pin, city, state = place
                lat, lng = jitter(alat, alng)
                first, last = person(i + 100)
                store = f"{rng.choice(STORE_WORDS)} {rng.choice(['Medical', 'Chemist', 'Pharmacy', 'Medicos'])} {area} (Demo)"
                offers_delivery = rng.random() < 0.75
                flat = rng.choice([None, None, 5, 10, 12, 15, 18, 20])
                uid = add_user("pharmacy", f"{first} {last}", f"pharmacy{i:02d}@{DEMO_DOMAIN}",
                               f"97{rng.randint(10000000, 99999999)}")
                conn.execute(
                    """INSERT INTO pharmacies (user_id, slug, store_name, drug_licence_no, pharmacist_name, pharmacist_reg_no,
                       gstin, address, city, pincode, lat, lng, opening_hours, offers_delivery, delivery_radius_km,
                       delivery_fee_paise, accepts_online_payment, status, created_at, approved_at, updated_at,
                       flat_discount_percent, pickup_ready_minutes, delivery_minutes, state)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, ?)""",
                    (uid, f"demo-pharmacy-{i}", store, f"DEMO-MH-MZ{i:03d}/20B/21B", f"{first} {last}", f"DEMO-PH-{i:04d}",
                     f"Shop {rng.randint(1, 40)}, {rng.choice(STREETS)}, {area}", city, pin, lat, lng,
                     rng.choice(["8 AM – 11 PM", "9 AM – 10 PM", "24 hours", "8 AM – 10 PM"]), int(offers_delivery),
                     rng.choice([2, 3, 4, 5]), rng.choice([0, 20, 30, 40, 50]) * 100, int(rng.random() < 0.5),
                     now, now, now, flat, rng.choice([10, 15, 20, 30]), rng.choice([20, 30, 45, 60, 90]), state))
                # Price list: a random share of the catalogue, 3–25 % below MRP, a few out of stock
                prices = {}
                for med in rng.sample(catalogue, k=rng.randint(len(catalogue) // 2, len(catalogue))):
                    price = int(med["mrp_paise"] * (1 - rng.uniform(0.03, 0.25)))
                    prices[med["id"]] = price
                    conn.execute("INSERT INTO pharmacy_stock (pharmacy_id, medicine_id, mrp_paise, price_paise, in_stock, "
                                 "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                                 (uid, med["id"], med["mrp_paise"], price, int(rng.random() > 0.08), now))
                pharmacy_rows.append({"id": uid, "lat": lat, "lng": lng, "prices": prices})

            for i in range(1, pharmacies_n + 1):
                area, alat, alng, pin = AREAS[(i * 3) % len(AREAS)]
                add_pharmacy(i, (area, alat, alng, pin, "Mumbai", "Maharashtra"))
            for k, (city, state, clat, clng, pin, _) in enumerate(OTHER_CITIES):
                for j in range(2):
                    add_pharmacy(pharmacies_n + 1 + k * 2 + j, (city, clat, clng, pin, city, state))

            # ---- Labs ----
            tests = {r["name"]: r for r in conn.execute("SELECT * FROM lab_tests")}
            lab_rows = []
            lab_places = [(area, alat, alng, pin, "Mumbai", "Maharashtra") for area, alat, alng, pin in AREAS] + \
                [(area, alat, alng, pin, "Mumbai", "Maharashtra") for area, alat, alng, pin in AREAS[::4]] + \
                [(c, la, ln, pin, c, st) for c, st, la, ln, pin, _ in OTHER_CITIES]
            for i, (area, alat, alng, pin, city, state) in enumerate(lab_places, 1):
                lat, lng = jitter(alat, alng)
                first, last = person(i + 500)
                name = f"{rng.choice(['Metro', 'Precision', 'Lifecare', 'Accurate', 'Prime', 'Wellness', 'Sure'])} " \
                       f"{rng.choice(['Diagnostics', 'Pathology Lab', 'Labs', 'Imaging & Labs'])} {area} (Demo)"
                home = rng.random() < 0.7
                uid = add_user("lab", f"{first} {last}", f"lab{i:02d}@{DEMO_DOMAIN}", f"94{rng.randint(10000000, 99999999)}")
                report_hours = rng.choice([6, 12, 24, 24, 48])
                conn.execute(
                    """INSERT INTO labs (user_id, slug, lab_name, registration_no, nabl_no, pathologist_name, pathologist_reg_no,
                       address, city, state, pincode, lat, lng, opening_hours, home_collection, collection_fee_paise,
                       collection_radius_km, flat_discount_percent, report_hours, status, created_at, approved_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?)""",
                    (uid, f"demo-lab-{i}", name, f"DEMO-CEA-{i:04d}", f"DEMO-MC-{i:04d}" if rng.random() < 0.5 else None,
                     f"Dr. {first} {last}", f"DEMO-MMC-{i:04d}", f"{rng.randint(1, 80)}, {rng.choice(STREETS)}, {area}", city,
                     state, pin, lat, lng, "7 AM – 9 PM", int(home), rng.choice([0, 50, 100, 150]) * 100 if home else 0,
                     rng.choice([3, 4, 5]), rng.choice([None, 10, 15, 20, 25]), report_hours, now, now, now))
                prices = {}
                for t in rng.sample(list(tests.values()), k=rng.randint(len(tests) * 2 // 3, len(tests))):
                    if not t["mrp_paise"]:
                        continue
                    price = int(t["mrp_paise"] * rng.uniform(0.55, 0.95)) // 100 * 100
                    prices[t["id"]] = price
                    conn.execute("INSERT INTO lab_prices (lab_id, test_id, mrp_paise, price_paise, available, report_hours, "
                                 "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                                 (uid, t["id"], t["mrp_paise"], price, report_hours * (2 if t["category"] == "Imaging" else 1)
                                  if t["category"] != "Imaging" else rng.choice([2, 4, 24]), now))
                lab_rows.append({"id": uid, "lat": lat, "lng": lng, "prices": prices, "name": name,
                                 "home": home, "radius": 4})

            # ---- Patients to log in as (consent given, home set) ----
            for i in range(1, patients_n + 1):
                area, alat, alng, pin = AREAS[(i * 4) % len(AREAS)]
                lat, lng = jitter(alat, alng, 0.5)
                first, last = person(i + 200)
                add_user("patient", f"{first} {last} (Demo)", f"patient{i}@{DEMO_DOMAIN}", f"99{rng.randint(10000000, 99999999)}",
                         home_lat=lat, home_lng=lng, home_address=f"Flat {rng.randint(101, 1204)}, {area}, Mumbai {pin}",
                         share_consent_at=now, dob=f"{rng.randint(1960, 2002)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
                         sex=rng.choice(["F", "M"]), city="Mumbai", state="Maharashtra")

            # ---- Medical representatives & meeting requests ----
            mr_ids = []
            for k in range(12):
                company_id, division_id, areas = divisions[k % len(divisions)]
                city, state = ("Mumbai", "Maharashtra") if k < 9 else [("Pune", "Maharashtra"), ("Bengaluru", "Karnataka"),
                                                                       ("Delhi", "Delhi")][k - 9]
                first, last = person(k + 400)
                uid = add_user("mr", f"{first} {last} (Demo)", f"mr{k + 1:02d}@{DEMO_DOMAIN}", f"95{rng.randint(10000000, 99999999)}")
                conn.execute("INSERT INTO mrs (user_id, company_id, division_id, employee_code, territory_city, "
                             "territory_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (uid, company_id, division_id, f"DEMO-E{k + 1:03d}", city, state, now))
                mr_ids.append((uid, company_id, division_id, city))
            for uid, company_id, division_id, city in mr_ids:
                local = [d for d in doctor_rows if d["city"] == city and d["category"] in ("general_physician", "specialist")]
                for doc in rng.sample(local, k=min(len(local), rng.randint(2, 5))):
                    created = settings.now() - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 9))
                    status = rng.choice(["pending", "accepted", "completed", "completed", "declined"])
                    when = (created + timedelta(days=rng.randint(2, 10))).replace(hour=rng.choice([13, 14, 16, 19]), minute=0)
                    conn.execute("INSERT INTO mr_requests (mr_id, doctor_id, company_id, division_id, purpose, products, "
                                 "preferred_date, preferred_time, status, doctor_note, scheduled_for, created_at, updated_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (uid, doc["id"], company_id, division_id, rng.choice(
                                     ["Share new clinical data", "Product introduction", "Patient education material",
                                      "Follow-up on samples policy"]), None, (created + timedelta(days=3)).date().isoformat(),
                                  "16:00", status, "10 minutes after OPD" if status == "accepted" else None,
                                  when.isoformat(timespec="minutes") if status in ("accepted", "completed") else None,
                                  stamp(created), stamp(created + timedelta(days=1))))

            # ---- Six months of history: visits, revisits, coded diagnoses, prescriptions, refills, orders ----
            history_counts = seed_history(conn, add_user, doctor_rows, pharmacy_rows, history_patients, lab_rows)
            history_counts.update(seed_everyday_care(conn))

        counts = {role: conn.execute("SELECT COUNT(*) FROM users WHERE role = ? AND email LIKE ?",
                                     (role, f"%@{DEMO_DOMAIN}")).fetchone()[0]
                  for role in ("doctor", "pharmacy", "patient", "company", "mr", "lab")}
    seed.history = history_counts
    return counts


def demo_pdf(title: str, lines: list) -> bytes:
    """A tiny valid one-page PDF with a few lines of text (so demo reports open in a browser)."""
    text = [f"BT /F1 16 Tf 50 790 Td ({title}) Tj ET", "BT /F1 10 Tf 50 770 Td (DEMO REPORT - not a real patient) Tj ET"]
    for n, line in enumerate(lines):
        safe = line.replace("(", "[").replace(")", "]")
        text.append(f"BT /F1 12 Tf 50 {740 - n * 18} Td ({safe}) Tj ET")
    stream = "\n".join(text).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# Demo results per test: (value for someone with the related condition, value otherwise)
RESULTS = {
    "HbA1c (glycated haemoglobin)": ("E11.9", lambda: round(rng.uniform(6.6, 9.4), 1), lambda: round(rng.uniform(4.8, 5.7), 1)),
    "Fasting Blood Sugar (FBS)": ("E11.9", lambda: rng.randint(118, 190), lambda: rng.randint(78, 99)),
    "Post-prandial Blood Sugar (PPBS)": ("E11.9", lambda: rng.randint(160, 260), lambda: rng.randint(95, 138)),
    "TSH": ("E03.9", lambda: round(rng.uniform(4.6, 9.5), 2), lambda: round(rng.uniform(0.8, 3.8), 2)),
    "Serum Creatinine": ("N18.9", lambda: round(rng.uniform(1.4, 2.6), 2), lambda: round(rng.uniform(0.7, 1.2), 2)),
    "Haemoglobin": ("D50.9", lambda: round(rng.uniform(8.5, 11.5), 1), lambda: round(rng.uniform(12.5, 15.5), 1)),
    "Vitamin B12": ("E53.8", lambda: rng.randint(110, 190), lambda: rng.randint(250, 700)),
    "Uric Acid": ("M10.9", lambda: round(rng.uniform(7.5, 9.8), 1), lambda: round(rng.uniform(4, 6.8), 1)),
    "PSA (prostate)": ("N40", lambda: round(rng.uniform(3, 6.5), 1), lambda: round(rng.uniform(0.5, 2.5), 1)),
    "Platelet Count": ("A90", lambda: round(rng.uniform(0.6, 1.4), 2), lambda: round(rng.uniform(1.6, 3.8), 2)),
}
PANELS = {  # panel test -> the key results reported under it
    "Lipid Profile": [("Total Cholesterol", "E78.5", (210, 280), (140, 195)), ("LDL Cholesterol", "E78.5", (120, 190), (60, 99)),
                      ("Triglycerides", "E78.5", (160, 260), (80, 145))],
    "Complete Blood Count (CBC)": [("Haemoglobin", "D50.9", (8.5, 11.5), (12.5, 15.5)), ("Platelet Count", "A90", (0.6, 1.4), (1.6, 3.8))],
    "Thyroid Profile (T3, T4, TSH)": [("TSH", "E03.9", (4.6, 9.5), (0.8, 3.8))],
    "Kidney Function Test (KFT)": [("Serum Creatinine", "N18.9", (1.4, 2.6), (0.7, 1.2)), ("Blood Urea", "N18.9", (45, 80), (18, 38))],
    "Liver Function Test (LFT)": [("SGPT (ALT)", "K76.0", (60, 140), (12, 45))],
}
TEXT_RESULTS = {"Dengue NS1 Antigen": ("A90", "Positive", "Negative"), "Malaria Antigen (rapid)": ("B54", "Positive", "Negative"),
                "Widal Test": ("A01.0", "Positive 1:160", "Negative"), "Urine Routine & Microscopy": ("N39.0", "Pus cells 15-20/hpf", "Normal"),
                "ECG": ("I25.1", "ST-T changes", "Normal sinus rhythm"), "Chest X-ray (PA view)": ("J18.9", "Patchy consolidation", "Normal")}
OTHER_MEDICINES = [("Shelcal 500", "1-0-0", "Family doctor"), ("Becosules", "1-0-0", "Self"), ("Limcee 500", "0-1-0", "Self"),
                   ("Isabgol husk", "0-0-1", "Family doctor"), ("Ayurvedic chyawanprash", "1 spoon daily", "Self"),
                   ("Omega-3 capsules", "0-1-0", "Cardiologist (outside)")]


def seed_history(conn, add_user, doctor_rows, pharmacy_rows, patients: int, lab_rows=()) -> dict:
    meds = {r["name"]: r for r in conn.execute("SELECT * FROM medicines")}
    lab_tests = {r["name"]: r for r in conn.execute("SELECT * FROM lab_tests")}
    demo_dir = Path(settings.UPLOAD_DIR) / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    conditions = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM conditions")}
    prescribers = [d for d in doctor_rows if d["category"] in ("general_physician", "specialist")]
    today = settings.now()
    stats = {"appointments": 0, "prescriptions": 0, "refills": 0, "orders": 0, "lab_bookings": 0, "reports": 0}

    def result_values(test_name, codes):
        """[(name, value text, test row)] for one test, sick or healthy depending on the patient's diagnoses."""
        if test_name in RESULTS:
            code, sick, well = RESULTS[test_name]
            return [(test_name.split(" (")[0], str((sick if code in codes else well)()), lab_tests.get(test_name))]
        if test_name in PANELS:
            out = []
            for name, code, sick, well in PANELS[test_name]:
                lo, hi = sick if code in codes else well
                value = round(rng.uniform(lo, hi), 1) if hi < 20 else rng.randint(int(lo), int(hi))
                out.append((name, str(value), lab_tests.get(name)))
            return out
        if test_name in TEXT_RESULTS:
            code, sick, well = TEXT_RESULTS[test_name]
            return [(test_name, sick if code in codes else well, None)]
        return [(test_name, "Within normal limits", None)]

    def add_tests(rx_id, doctor, patient, created, codes):
        names = list(dict.fromkeys(n for c in codes for n in ref.CONDITION_TESTS.get(c, [])))[:4]
        if not names:
            return
        rows = []
        for n in names:
            cur = conn.execute("INSERT INTO prescription_tests (prescription_id, test_id, name) VALUES (?, ?, ?)",
                               (rx_id, lab_tests[n]["id"], n))
            rows.append((cur.lastrowid, lab_tests[n]))
        near = [l for l in patient["labs"] if all(t["id"] in l["prices"] for _, t in rows)] or patient["labs"]
        for lab in near[:6]:
            conn.execute("INSERT OR IGNORE INTO lab_requests (prescription_id, lab_id, distance_km, status, created_at, updated_at) "
                         "VALUES (?, ?, ?, 'open', ?, ?)", (rx_id, lab["id"], round(((lab["lat"] - patient["lat"]) ** 2 + (lab["lng"] - patient["lng"]) ** 2) ** .5 * 111, 2),
                                                            stamp(created), stamp(created)))
        if not near or rng.random() > 0.75:
            return
        lab = min(near[:6], key=lambda l: sum(l["prices"].get(t["id"], 10 ** 9) for _, t in rows))  # most book the cheapest
        priced = [(pt_id, t) for pt_id, t in rows if t["id"] in lab["prices"]]
        if not priced:
            return
        age_days = (today - created).days
        status = "completed" if age_days > 6 else rng.choice(["booked", "accepted", "sample_collected", "report_ready", "completed"])
        home = lab["home"] and rng.random() < 0.5
        subtotal = sum(lab["prices"][t["id"]] for _, t in priced)
        fee = 5000 if home else 0
        booking_id = str(uuid.uuid4())
        done = created + timedelta(days=1)
        conn.execute("INSERT INTO lab_bookings (id, reference, prescription_id, lab_id, patient_id, collection, preferred_date, "
                     "address, distance_km, subtotal_paise, collection_fee_paise, total_paise, payment_status, status, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, ?)",
                     (booking_id, new_reference(conn, "lab_bookings", "LB"), rx_id, lab["id"], patient["id"],
                      "home" if home else "visit", (created + timedelta(days=1)).date().isoformat(),
                      patient["address"] if home else None, subtotal, fee, subtotal + fee,
                      "paid" if status == "completed" else "unpaid", status, stamp(created + timedelta(hours=2)), stamp(done)))
        conn.executemany("INSERT INTO lab_booking_items (booking_id, prescription_test_id, test_id, name, price_paise) "
                         "VALUES (?, ?, ?, ?, ?)", [(booking_id, pt_id, t["id"], t["name"], lab["prices"][t["id"]]) for pt_id, t in priced])
        conn.execute("UPDATE lab_requests SET status = 'closed' WHERE prescription_id = ? AND lab_id != ?", (rx_id, lab["id"]))
        stats["lab_bookings"] += 1
        if status not in ("report_ready", "completed"):
            return
        values = [v for _, t in priced for v in result_values(t["name"], codes)]
        rel = f"demo/{uuid.uuid4().hex}.pdf"
        pdf = demo_pdf(lab["name"][:60], [f"Patient ref: {patient['id']}  Date: {done.date()}"] +
                       [f"{n}: {val} {(t['unit'] or '') if t else ''}" for n, val, t in values])
        (Path(settings.UPLOAD_DIR) / rel).write_bytes(pdf)
        cur = conn.execute("INSERT INTO health_records (patient_id, kind, title, record_date, file_path, file_name, content_type, "
                           "size_bytes, uploaded_by, uploader_role, lab_booking_id, lab_id, doctor_id, share_with_doctors, created_at) "
                           "VALUES (?, 'lab_report', ?, ?, ?, 'report.pdf', 'application/pdf', ?, ?, 'lab', ?, ?, ?, 1, ?)",
                           (patient["id"], ", ".join(t["name"] for _, t in priced)[:200], done.date().isoformat(), rel, len(pdf),
                            lab["id"], booking_id, lab["id"], doctor["id"], stamp(done)))
        for name, val, t in values:
            number = None
            try:
                number = float(val)
            except ValueError:
                pass
            low, high = (t["ref_low"], t["ref_high"]) if t else (None, None)
            flag = None if number is None or (low is None and high is None) else \
                "low" if low is not None and number < low else "high" if high is not None and number > high else "normal"
            conn.execute("INSERT INTO record_values (record_id, test_id, name, value_num, value_text, unit, ref_low, ref_high, flag) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (cur.lastrowid, t["id"] if t else None, name, number,
                                                                None if number is not None else val, t["unit"] if t else None,
                                                                low, high, flag))
        stats["reports"] += 1

    def pick_condition(band, sex, when):
        pool = [(c, w) for c, w in BY_AGE[band] if ":" not in c or c.endswith(":" + sex)]
        if when.month in (6, 7, 8, 9):
            pool += MONSOON
        codes, weights = zip(*pool)
        return rng.choices(codes, weights)[0].split(":")[0]

    def insert_rx(appt_id, doctor, patient, created, codes, age, refills=0, parent=None, items=None):
        rx_id = str(uuid.uuid4())
        conn.execute("INSERT INTO prescriptions (id, reference, appointment_id, doctor_id, patient_id, patient_age, "
                     "patient_sex, diagnosis, advice, lat, lng, location_source, radius_km, status, created_at, valid_until, "
                     "parent_id, refill_number, refills_allowed, refills_used, refill_until) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'home', ?, 'active', ?, ?, ?, ?, ?, 0, ?)",
                     (rx_id, new_reference(conn, "prescriptions", "RX"), appt_id, doctor["id"], patient["id"], age,
                      patient["sex"], "Demo prescription", patient["lat"], patient["lng"], settings.NEARBY_RADIUS_KM,
                      stamp(created), stamp(created + timedelta(days=settings.RX_VALID_DAYS)),
                      parent["id"] if parent else None, parent["number"] if parent else 0, 0 if parent else refills,
                      stamp(created + timedelta(days=settings.LONG_TERM_RX_DAYS)) if refills and not parent else None))
        conn.executemany("INSERT OR IGNORE INTO prescription_conditions (prescription_id, condition_id) VALUES (?, ?)",
                         [(rx_id, conditions[c]) for c in codes])
        lines = []
        if items is None:
            items = []
            for code in codes:
                for choices, dosage, qty in TREATMENT.get(code, [(["Dolo 650"], "1-1-1", 10)]):
                    items.append((rng.choice(choices), dosage, qty, code in LONG_TERM))
        seen = set()
        for name, dosage, qty, long_term in items:
            if name in seen:
                continue
            seen.add(name)
            m = meds[name]
            cur = conn.execute("INSERT INTO prescription_items (prescription_id, medicine_id, name, generic_name, strength, "
                               "form, dosage, duration, quantity, allow_substitute, daily_units, duration_days, is_long_term) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 1, ?, ?, ?)",
                               (rx_id, m["id"], m["name"], m["generic_name"], m["strength"], m["form"], dosage, qty,
                                prescriptions.daily_units_from(dosage), None if prescriptions.daily_units_from(dosage) else 30,
                                int(long_term)))
            lines.append({"item_id": cur.lastrowid, "medicine": m, "qty": qty, "dosage": dosage, "long_term": long_term})
        stats["prescriptions"] += 1
        return rx_id, lines

    def order(rx_id, patient, lines, created):
        """A completed pick-up order at one of the pharmacies near the patient, and its medicine courses."""
        if not patient["pharmacies"] or created > today - timedelta(hours=6):
            return
        pharmacy = rng.choice(patient["pharmacies"])
        order_id = str(uuid.uuid4())
        rows, total = [], 0
        for line in lines:
            m = line["medicine"]
            pack_price = pharmacy["prices"].get(m["id"]) or int((m["mrp_paise"] or 5000) * 0.9)
            unit = max(1, round(pack_price / max(m["pack_size"], 1)))
            rows.append((line["item_id"], m["id"], m["name"], line["qty"], unit, unit * line["qty"]))
            total += unit * line["qty"]
        done = created + timedelta(hours=rng.randint(1, 30))
        conn.execute("INSERT INTO medicine_orders (id, reference, prescription_id, pharmacy_id, patient_id, source, "
                     "subtotal_paise, delivery_fee_paise, total_paise, fulfilment, payment_method, payment_status, status, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'price_list', ?, 0, ?, 'pickup', 'pharmacy', 'paid', "
                     "'completed', ?, ?)", (order_id, new_reference(conn, "medicine_orders", "MO"), rx_id, pharmacy["id"],
                                            patient["id"], total, total, stamp(created), stamp(done)))
        conn.executemany("INSERT INTO order_items (order_id, prescription_item_id, medicine_id, name, quantity, "
                         "unit_price_paise, line_paise) VALUES (?, ?, ?, ?, ?, ?, ?)", [(order_id, *r) for r in rows])
        medicine_courses.record_courses(conn, order_id, started=done.date())
        stats["orders"] += 1

    for i in range(patients):
        # Most patients live in Mumbai; some in the other demo cities
        if rng.random() < 0.8:
            area, alat, alng, pin = rng.choice(AREAS)
            city, state = "Mumbai", "Maharashtra"
        else:
            city, state, alat, alng, pin, _ = rng.choice(OTHER_CITIES)
            area = city
        lat, lng = jitter(alat, alng, 1.2)
        band = rng.choices(["child", "young", "middle", "older"], [12, 30, 33, 25])[0]
        age_now = {"child": rng.randint(1, 12), "young": rng.randint(16, 29), "middle": rng.randint(30, 52),
                   "older": rng.randint(53, 82)}[band]
        first, last = FIRST[rng.randrange(len(FIRST))], LAST[rng.randrange(len(LAST))]
        sex = "F" if first in FEMALE else "M"
        dob = (today - timedelta(days=age_now * 365 + rng.randint(0, 364))).date().isoformat()
        uid = add_user("patient", f"{first} {last} (Demo)", f"history{i + 1:03d}@{DEMO_DOMAIN}",
                       f"93{rng.randint(10000000, 99999999)}", home_lat=lat, home_lng=lng,
                       home_address=f"Flat {rng.randint(101, 1204)}, {area}, {city} {pin}", share_consent_at=stamp(),
                       dob=dob, sex=sex, city=city, state=state)
        local = [d for d in prescribers if d["city"] == city] or prescribers
        local.sort(key=lambda d: (d["lat"] - lat) ** 2 + (d["lng"] - lng) ** 2)
        near_pharmacies = [p for p in pharmacy_rows if (p["lat"] - lat) ** 2 + (p["lng"] - lng) ** 2 < (3 / 111) ** 2]
        near_labs = sorted([l for l in lab_rows if (l["lat"] - lat) ** 2 + (l["lng"] - lng) ** 2 < (3 / 111) ** 2],
                           key=lambda l: (l["lat"] - lat) ** 2 + (l["lng"] - lng) ** 2)
        patient = {"id": uid, "sex": sex, "lat": lat, "lng": lng, "pharmacies": near_pharmacies, "labs": near_labs,
                   "address": f"{area}, {city} {pin}"}
        for name, dosage, by in rng.sample(OTHER_MEDICINES, k=rng.choice([0, 0, 1, 2])):
            conn.execute("INSERT INTO patient_medicines (patient_id, name, dosage, since, prescribed_by, active, created_at, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uid, name, dosage, (today - timedelta(days=rng.randint(30, 900))).date().isoformat(),
                                                                       by, int(rng.random() < 0.8), stamp(), stamp()))
        regular = local[rng.randrange(min(3, len(local)))]  # most people keep going back to the same doctor

        visit = today - timedelta(days=rng.randint(5, 180))
        while visit < today - timedelta(days=1):
            doctor = regular if rng.random() < 0.75 else rng.choice(local[:8])
            visit = visit.replace(hour=rng.choice([10, 11, 12, 17, 18, 19]), minute=rng.choice([0, 30]), second=0)
            appt_id = str(uuid.uuid4())
            conn.execute("INSERT INTO appointments (id, reference, doctor_id, patient_id, service_id, service_name, duration, "
                         "fee, appointment_date, appointment_time, status, payment_method, payment_status, created_at, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, 'Consultation', 30, ?, ?, ?, 'completed', 'clinic', 'paid', ?, ?)",
                         (appt_id, new_reference(conn, "appointments", "PY"), doctor["id"], uid, doctor["service_id"],
                          doctor["fee"], visit.date().isoformat(), visit.strftime("%H:%M"),
                          stamp(visit - timedelta(days=rng.randint(0, 4))), stamp(visit)))
            stats["appointments"] += 1
            age = age_now - (1 if (today - visit).days > 182 and rng.random() < 0.5 else 0)
            codes = [pick_condition(band, sex, visit)]
            if rng.random() < 0.25:
                codes.append(pick_condition(band, sex, visit))
            codes = list(dict.fromkeys(codes))
            chronic = any(c in LONG_TERM for c in codes)
            refills = rng.choice([2, 3]) if chronic else 0
            rx_id, lines = insert_rx(appt_id, doctor, patient, visit + timedelta(minutes=20), codes, age, refills)
            if rng.random() < (0.85 if chronic else 0.45):
                add_tests(rx_id, doctor, patient, visit + timedelta(minutes=20), codes)
            if rng.random() < 0.7:
                order(rx_id, patient, lines, visit + timedelta(hours=1))
            # Pre-authorised refills of the long-term medicines, roughly monthly
            if refills:
                long_items = [(l["medicine"]["name"], l["dosage"], l["qty"], True) for l in lines if l["long_term"]]
                when, used = visit, 0
                while used < refills and long_items:
                    when = when + timedelta(days=rng.randint(26, 33))
                    if when > today - timedelta(days=1):
                        break
                    used += 1
                    refill_id, refill_lines = insert_rx(None, doctor, patient, when, codes, age, parent={"id": rx_id, "number": used},
                                                        items=long_items)
                    conn.execute("UPDATE prescriptions SET refills_used = ? WHERE id = ?", (used, rx_id))
                    stats["refills"] += 1
                    if rng.random() < 0.85:
                        order(refill_id, patient, refill_lines, when + timedelta(hours=2))
            # Chronic patients come back every 2–3 months; others now and then
            gap = rng.randint(55, 95) if chronic else rng.randint(30, 160)
            visit = visit + timedelta(days=gap)
    return stats


FAMILY = [("spouse", 1975, 1995), ("son", 2005, 2020), ("daughter", 2005, 2020), ("mother", 1945, 1965),
          ("father", 1942, 1962)]
ALLERGIES = [None, None, None, None, "Penicillin", "Sulfa drugs", "Peanuts", "Dust", "NSAIDs"]
CONDITIONS = [None, None, None, "Diabetes", "High blood pressure", "Asthma", "Thyroid"]
BLOOD = ["A+", "B+", "O+", "AB+", "O+", "B+", "A-", "O-"]


def seed_everyday_care(conn) -> dict:
    """Video consults, health profiles, family members, follow-ups, dose records and notifications for the demo."""
    today = settings.now().date()
    demo = f"%@{DEMO_DOMAIN}"
    # About 40 % of doctors also consult by video
    video_docs = [r["user_id"] for r in conn.execute("SELECT d.user_id FROM doctors d JOIN users u ON u.id = d.user_id "
                                                     "WHERE u.email LIKE ?", (demo,)) if rng.random() < 0.4]
    for uid in video_docs:
        conn.execute("UPDATE doctors SET offers_video = 1, video_fee = ? WHERE user_id = ?", (rng.choice([None, 300, 400, 500]), uid))
    # Some upcoming appointments with those doctors are video consults
    upcoming = conn.execute(f"SELECT id FROM appointments WHERE status = 'confirmed' AND appointment_date >= ? AND visit_type = 'clinic' "
                            f"AND doctor_id IN ({','.join('?' * len(video_docs))})", (today.isoformat(), *video_docs)).fetchall() \
        if video_docs else []
    videos = 0
    # Each demo login patient also has an upcoming video consult (patient1's starts within the hour, to try "Join")
    logins = conn.execute("SELECT id FROM users WHERE role = 'patient' AND email LIKE ? ORDER BY email LIMIT 3",
                          (f"patient%@{DEMO_DOMAIN}",)).fetchall()
    soon = settings.now() + timedelta(minutes=30)
    soon = soon.replace(minute=0 if soon.minute < 30 else 30, second=0, microsecond=0)
    for k, pt in enumerate(logins):
        if not video_docs:
            break
        doc = video_docs[k % len(video_docs)]
        svc = conn.execute("SELECT * FROM services WHERE doctor_id = ? ORDER BY id LIMIT 1", (doc,)).fetchone()
        fee = conn.execute("SELECT video_fee FROM doctors WHERE user_id = ?", (doc,)).fetchone()["video_fee"] or svc["fee"]
        when = soon if k == 0 else (settings.now() + timedelta(days=k + 1)).replace(hour=11, minute=0)
        conn.execute("INSERT INTO appointments (id, reference, doctor_id, patient_id, service_id, service_name, duration, fee, "
                     "appointment_date, appointment_time, status, reason, payment_method, payment_status, created_at, updated_at, "
                     "visit_type, video_room) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 'clinic', 'unpaid', ?, ?, 'video', ?)",
                     (str(uuid.uuid4()), new_reference(conn, "appointments", "PY"), doc, pt["id"], svc["id"], svc["name"],
                      svc["duration"], fee, when.date().isoformat(), when.strftime("%H:%M"), "Follow-up on reports (demo)",
                      stamp(), stamp(), new_room()))
        videos += 1
    for r in upcoming:
        if rng.random() < 0.35:
            conn.execute("UPDATE appointments SET visit_type = 'video', video_room = ? WHERE id = ?", (new_room(), r["id"]))
            videos += 1

    # Health profiles and family members
    patients = conn.execute("SELECT id, email, name FROM users WHERE role = 'patient' AND email LIKE ?", (demo,)).fetchall()
    family = 0
    for pt in patients:
        login = pt["email"].startswith("patient")
        if login or rng.random() < 0.35:
            conn.execute("UPDATE users SET blood_group = ?, allergies = ?, chronic_conditions = ?, emergency_name = ?, "
                         "emergency_phone = ? WHERE id = ?",
                         (rng.choice(BLOOD), rng.choice(ALLERGIES), rng.choice(CONDITIONS), f"{person(pt['id'] + 900)[0]} (Demo)",
                          f"98{rng.randint(10000000, 99999999)}", pt["id"]))
        for relation, y1, y2 in rng.sample(FAMILY, k=rng.randint(1, 3) if login else (1 if rng.random() < 0.15 else 0)):
            first = person(pt["id"] * 7 + len(relation))[0]
            last = pt["name"].replace(" (Demo)", "").split()[-1]
            conn.execute("INSERT INTO family_members (patient_id, name, relation, dob, sex, blood_group, allergies, conditions, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (pt["id"], f"{first} {last}", relation, f"{rng.randint(y1, y2)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
                          "F" if relation in ("daughter", "mother") else "M" if relation in ("son", "father") else rng.choice("FM"),
                          rng.choice(BLOOD), rng.choice(ALLERGIES), rng.choice(CONDITIONS), stamp()))
            family += 1
    # A few past visits were for a family member
    for pt in patients:
        members = [r["id"] for r in conn.execute("SELECT id FROM family_members WHERE patient_id = ?", (pt["id"],))]
        if not members:
            continue
        for a in conn.execute("SELECT id FROM appointments WHERE patient_id = ?", (pt["id"],)).fetchall():
            if rng.random() < 0.25:
                m = rng.choice(members)
                conn.execute("UPDATE appointments SET family_member_id = ? WHERE id = ?", (m, a["id"]))
                conn.execute("UPDATE prescriptions SET family_member_id = ? WHERE appointment_id = ?", (m, a["id"]))

    # Follow-ups on about a quarter of last month's prescriptions
    follow_ups = 0
    for r in conn.execute("SELECT id, created_at FROM prescriptions WHERE status = 'active' AND parent_id IS NULL AND created_at >= ? "
                          "AND patient_id IN (SELECT id FROM users WHERE email LIKE ?)",
                          (stamp(settings.now() - timedelta(days=30)), demo)).fetchall():
        if rng.random() < 0.25:
            due = datetime.fromisoformat(r["created_at"]).date() + timedelta(days=rng.choice([14, 21, 30, 45]))
            conn.execute("UPDATE prescriptions SET follow_up_on = ? WHERE id = ?", (due.isoformat(), r["id"]))
            follow_ups += 1

    # Demo login patients also take a couple of medicines they listed themselves (so "Today" always has doses)
    own = [("Thyronorm 50 mcg", "1-0-0", "Empty stomach"), ("Metformin 500 mg", "1-0-1", "After food"),
           ("Atorvastatin 10 mg", "0-0-1", None), ("Vitamin B12 1500 mcg", "0-1-0", None)]
    for pt in conn.execute("SELECT id FROM users WHERE role = 'patient' AND email LIKE ?", (f"patient%@{DEMO_DOMAIN}",)).fetchall():
        for name, dosage, notes in rng.sample(own, k=2):
            conn.execute("INSERT INTO patient_medicines (patient_id, name, dosage, since, prescribed_by, notes, active, created_at, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                         (pt["id"], name, dosage, (today - timedelta(days=rng.randint(20, 300))).isoformat(),
                          "Family doctor (Demo)", notes, stamp(), stamp()))

    # Dose records for the last week (about 80 % taken) for people with current medicines
    doses = 0
    start = today - timedelta(days=6)
    for pt in conn.execute("SELECT * FROM users WHERE role = 'patient' AND email LIKE ?", (demo,)).fetchall():
        if not pt["email"].startswith("patient") and rng.random() > 0.3:
            continue
        habit = rng.uniform(0.6, 0.97)
        first = today - timedelta(days=29) if pt["email"].startswith("patient") else start  # logins: a full month
        for med, day, slot in medicine_reminders.scheduled_doses(conn, pt["id"], first, today, medicine_reminders.times_for(pt)):
            roll = rng.random()
            if roll > habit + 0.05:
                continue  # not marked
            conn.execute("INSERT OR IGNORE INTO dose_logs (patient_id, source, source_id, dose_date, slot, name, status, logged_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (pt["id"], med["source"], med["source_id"], day.isoformat(), slot,
                                                             med["name"], "taken" if roll <= habit else "skipped", stamp()))
            doses += 1
        if pt["email"].startswith("patient"):
            conn.execute("UPDATE users SET dose_email_digest = 1 WHERE id = ?", (pt["id"],))

    # A few recent notifications for every demo login, from their own data
    notes = 0

    def note(uid, kind, title, body, link, hours_ago, read):
        nonlocal notes
        when = settings.now() - timedelta(hours=hours_ago)
        conn.execute("INSERT INTO notifications (user_id, kind, title, body, link, created_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (uid, kind, title, body, link, stamp(when), stamp(when) if read else None))
        notes += 1

    for u in conn.execute("SELECT id, role, email FROM users WHERE email LIKE ? AND (email LIKE 'patient%' OR email LIKE 'doctor0%' "
                          "OR email LIKE 'pharmacy0%' OR email LIKE 'lab0%')", (demo,)).fetchall():
        if u["role"] == "patient":
            for i, a in enumerate(conn.execute("SELECT a.*, du.name AS doctor_name FROM appointments a JOIN users du ON du.id = a.doctor_id "
                                               "WHERE a.patient_id = ? ORDER BY a.created_at DESC LIMIT 2", (u["id"],))):
                note(u["id"], "appointment", f"Your appointment is confirmed – {a['appointment_date']} {a['appointment_time']} ({a['reference']})",
                     f"With {a['doctor_name']}", "/account#appointments", 30 + i * 20, i > 0)
            for b in conn.execute("SELECT reference FROM lab_bookings WHERE patient_id = ? AND status IN ('report_ready', 'completed') "
                                  "ORDER BY updated_at DESC LIMIT 1", (u["id"],)):
                note(u["id"], "lab", f"Test report ready ({b['reference']})", "Your lab uploaded the report and key results.",
                     "/account#records", 5, False)
            note(u["id"], "reminder", "Time for your morning medicines", "Tick each dose as you take it.", "/account#today", 2, False)
        elif u["role"] == "doctor":
            for i, a in enumerate(conn.execute("SELECT a.*, pu.name AS patient_name FROM appointments a JOIN users pu ON pu.id = a.patient_id "
                                               "WHERE a.doctor_id = ? AND a.appointment_date >= ? ORDER BY a.created_at DESC LIMIT 3",
                                               (u["id"], today.isoformat()))):
                note(u["id"], "appointment", f"New appointment booked – {a['appointment_date']} {a['appointment_time']} ({a['reference']})",
                     f"Patient: {a['patient_name']}", "/doctor", 3 + i * 9, i > 1)
        elif u["role"] == "pharmacy":
            note(u["id"], "order", "New prescription 1.2 km away", "Send a quote to win the order.", "/pharmacy", 4, False)
        elif u["role"] == "lab":
            note(u["id"], "lab", "Test request nearby – 3 test(s)", "Quote your price and report time.", "/lab", 6, False)
    return {"video_appointments": videos, "family_members": family, "follow_ups": follow_ups, "dose_logs": doses,
            "notifications": notes}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--wipe" in argv:
        print(f"Removed {wipe()} demo accounts.")
        return 0
    counts = seed()
    h = seed.history
    print(f"Added demo data: {counts['doctor']} doctors, {counts['pharmacy']} pharmacies, {counts['lab']} labs, "
          f"{counts['patient']} patients, {counts['company']} pharma companies, {counts['mr']} MRs.")
    print(f"History: {h['appointments']} visits, {h['prescriptions']} prescriptions ({h['refills']} refills), "
          f"{h['orders']} completed medicine orders, {h['lab_bookings']} lab bookings, {h['reports']} lab reports.")
    print(f"Everyday care: {h['family_members']} family members, {h['video_appointments']} video consults, "
          f"{h['follow_ups']} follow-ups, {h['dose_logs']} dose records, {h['notifications']} notifications.")
    print(f"Log in with e.g. patient1@{DEMO_DOMAIN}, doctor01@{DEMO_DOMAIN}, pharmacy01@{DEMO_DOMAIN}, "
          f"lab01@{DEMO_DOMAIN}, company1@{DEMO_DOMAIN} or mr01@{DEMO_DOMAIN} — password {PASSWORD}")
    print("Remove it all with:  python seed_demo.py --wipe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
