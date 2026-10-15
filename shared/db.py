"""SQLite connection helpers and schema."""
import sqlite3
from contextlib import contextmanager

from shared import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor', 'admin')),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS doctors (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    qualification TEXT NOT NULL,
    registration_no TEXT NOT NULL,
    registration_council TEXT,
    experience_years INTEGER NOT NULL DEFAULT 0,
    specialities TEXT NOT NULL DEFAULT '',
    bio TEXT,
    clinic_name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    pincode TEXT,
    slot_minutes INTEGER NOT NULL DEFAULT 30,
    accepts_clinic_payment INTEGER NOT NULL DEFAULT 1,
    accepts_online_payment INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS doctor_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    duration INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS blocked_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
    block_date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    service_id INTEGER REFERENCES services(id),
    service_name TEXT NOT NULL,
    duration INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('pending_payment', 'confirmed', 'completed', 'no_show', 'cancelled', 'expired')),
    reason TEXT,
    doctor_notes TEXT,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('clinic', 'online')),
    payment_status TEXT NOT NULL CHECK (payment_status IN
        ('unpaid', 'pending', 'paid', 'refunded', 'refund_failed', 'not_refundable')),
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    refund_id TEXT,
    cancelled_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reminder_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id TEXT NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    author_role TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_failures (
    email TEXT NOT NULL COLLATE NOCASE,
    failed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_failures ON login_failures(email, failed_at);
CREATE INDEX IF NOT EXISTS idx_appt_doctor_date ON appointments(doctor_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appt_patient ON appointments(patient_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appt_order ON appointments(razorpay_order_id);
CREATE INDEX IF NOT EXISTS idx_hours_doctor ON doctor_hours(doctor_id, weekday);
CREATE INDEX IF NOT EXISTS idx_blocks_doctor ON blocked_times(doctor_id, block_date);
CREATE INDEX IF NOT EXISTS idx_messages_appt ON messages(appointment_id);
CREATE INDEX IF NOT EXISTS idx_doctors_status_city ON doctors(status, city);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(settings.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_txn(conn):
    """Serialise writers so two requests can never take the same slot."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


# ---------- Versioned upgrades (PRAGMA user_version) ----------
NETWORK_SCHEMA = """
CREATE TABLE users_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor', 'admin', 'pharmacy')),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    home_address TEXT,
    home_lat REAL,
    home_lng REAL,
    share_consent_at TEXT
);
INSERT INTO users_v2 (id, role, name, email, phone, password_hash, is_active, created_at, last_login_at)
    SELECT id, role, name, email, phone, password_hash, is_active, created_at, last_login_at FROM users;
DROP TABLE users;
ALTER TABLE users_v2 RENAME TO users;

ALTER TABLE doctors ADD COLUMN category TEXT NOT NULL DEFAULT 'physiotherapist';
ALTER TABLE doctors ADD COLUMN lat REAL;
ALTER TABLE doctors ADD COLUMN lng REAL;
CREATE INDEX idx_doctors_geo ON doctors(lat, lng);

CREATE TABLE pharmacies (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    store_name TEXT NOT NULL,
    drug_licence_no TEXT NOT NULL,
    pharmacist_name TEXT NOT NULL,
    pharmacist_reg_no TEXT NOT NULL,
    gstin TEXT,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    pincode TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    opening_hours TEXT,
    offers_delivery INTEGER NOT NULL DEFAULT 0,
    delivery_radius_km REAL NOT NULL DEFAULT 3,
    delivery_fee_paise INTEGER NOT NULL DEFAULT 0,
    accepts_online_payment INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    updated_at TEXT
);
CREATE INDEX idx_pharmacies_geo ON pharmacies(status, lat, lng);

CREATE TABLE medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    generic_name TEXT,
    strength TEXT,
    form TEXT,
    pack_size INTEGER NOT NULL DEFAULT 1,
    rx_required INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_medicines_unique ON medicines(lower(name), lower(COALESCE(strength, '')), lower(COALESCE(form, '')));
CREATE INDEX idx_medicines_generic ON medicines(lower(generic_name), lower(strength), lower(form));

CREATE TABLE pharmacy_stock (
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    mrp_paise INTEGER NOT NULL,
    price_paise INTEGER NOT NULL,
    in_stock INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pharmacy_id, medicine_id)
);
CREATE INDEX idx_stock_medicine ON pharmacy_stock(medicine_id);

CREATE TABLE prescriptions (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    appointment_id TEXT REFERENCES appointments(id),
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    patient_age INTEGER,
    patient_sex TEXT,
    diagnosis TEXT,
    advice TEXT,
    lat REAL,
    lng REAL,
    location_source TEXT,
    radius_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX idx_rx_patient ON prescriptions(patient_id);
CREATE INDEX idx_rx_doctor ON prescriptions(doctor_id);

CREATE TABLE prescription_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    medicine_id INTEGER REFERENCES medicines(id),
    name TEXT NOT NULL,
    generic_name TEXT,
    strength TEXT,
    form TEXT,
    dosage TEXT,
    duration TEXT,
    quantity INTEGER NOT NULL,
    instructions TEXT,
    allow_substitute INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE rx_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id),
    distance_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'quoted', 'declined', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (prescription_id, pharmacy_id)
);
CREATE INDEX idx_requests_pharmacy ON rx_requests(pharmacy_id, status);

CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id),
    subtotal_paise INTEGER NOT NULL,
    delivery_available INTEGER NOT NULL DEFAULT 0,
    delivery_fee_paise INTEGER NOT NULL DEFAULT 0,
    ready_in_minutes INTEGER,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn', 'accepted', 'lost')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX idx_quotes_rx ON quotes(prescription_id, status);

CREATE TABLE quote_items (
    quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    prescription_item_id INTEGER NOT NULL REFERENCES prescription_items(id),
    available INTEGER NOT NULL,
    supplied_name TEXT,
    unit_price_paise INTEGER NOT NULL DEFAULT 0,
    quantity INTEGER NOT NULL,
    line_paise INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE medicine_orders (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    quote_id INTEGER REFERENCES quotes(id),
    source TEXT NOT NULL CHECK (source IN ('quote', 'price_list')),
    subtotal_paise INTEGER NOT NULL,
    delivery_fee_paise INTEGER NOT NULL DEFAULT 0,
    total_paise INTEGER NOT NULL,
    fulfilment TEXT NOT NULL CHECK (fulfilment IN ('pickup', 'delivery')),
    delivery_address TEXT,
    distance_km REAL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('pharmacy', 'online')),
    payment_status TEXT NOT NULL CHECK (payment_status IN
        ('unpaid', 'pending', 'paid', 'refunded', 'refund_failed')),
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    refund_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'placed', 'accepted', 'ready',
        'out_for_delivery', 'completed', 'rejected', 'cancelled', 'expired')),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_orders_pharmacy ON medicine_orders(pharmacy_id, status);
CREATE INDEX idx_orders_patient ON medicine_orders(patient_id);
CREATE INDEX idx_orders_rzp ON medicine_orders(razorpay_order_id);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES medicine_orders(id) ON DELETE CASCADE,
    prescription_item_id INTEGER NOT NULL REFERENCES prescription_items(id),
    medicine_id INTEGER REFERENCES medicines(id),
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price_paise INTEGER NOT NULL,
    line_paise INTEGER NOT NULL
);

CREATE TABLE payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payee_role TEXT NOT NULL CHECK (payee_role IN ('doctor', 'pharmacy')),
    payee_id INTEGER NOT NULL REFERENCES users(id),
    amount_paise INTEGER NOT NULL,
    reference TEXT,
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
"""

# Discounts off MRP and delivery speed, so patients can compare "best discount" and "fastest delivery"
OFFERS_SCHEMA = """
ALTER TABLE medicines ADD COLUMN mrp_paise INTEGER;
ALTER TABLE pharmacies ADD COLUMN flat_discount_percent REAL;
ALTER TABLE pharmacies ADD COLUMN pickup_ready_minutes INTEGER NOT NULL DEFAULT 15;
ALTER TABLE pharmacies ADD COLUMN delivery_minutes INTEGER NOT NULL DEFAULT 60;
ALTER TABLE quotes ADD COLUMN delivery_minutes INTEGER;
ALTER TABLE quote_items ADD COLUMN mrp_paise INTEGER;
"""

# Trust & safety: patient details, reviews, admin audit trail, delivery OTP, request throttling
TRUST_SCHEMA = """
ALTER TABLE users ADD COLUMN dob TEXT;
ALTER TABLE users ADD COLUMN sex TEXT;
ALTER TABLE users ADD COLUMN deleted_at TEXT;
ALTER TABLE medicine_orders ADD COLUMN delivery_otp TEXT;
ALTER TABLE medicine_orders ADD COLUMN reminded_at TEXT;
ALTER TABLE prescriptions ADD COLUMN expiry_reminded_at TEXT;

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    target_role TEXT NOT NULL CHECK (target_role IN ('doctor', 'pharmacy')),
    target_id INTEGER NOT NULL REFERENCES users(id),
    appointment_id TEXT UNIQUE REFERENCES appointments(id),
    order_id TEXT UNIQUE REFERENCES medicine_orders(id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_reviews_target ON reviews(target_role, target_id);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_audit_created ON audit_log(created_at);

CREATE TABLE rate_limits (
    key TEXT NOT NULL,
    hit_at TEXT NOT NULL
);
CREATE INDEX idx_rate_limits ON rate_limits(key, hit_at);
"""

# Pharma network & insights: company / MR accounts, states, structured diagnoses, refills, medicine courses
PHARMA_SCHEMA = """
CREATE TABLE users_v3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor', 'admin', 'pharmacy', 'company', 'mr')),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    home_address TEXT,
    home_lat REAL,
    home_lng REAL,
    share_consent_at TEXT,
    dob TEXT,
    sex TEXT,
    deleted_at TEXT,
    city TEXT,
    state TEXT
);
INSERT INTO users_v3 (id, role, name, email, phone, password_hash, is_active, created_at, last_login_at, home_address,
                      home_lat, home_lng, share_consent_at, dob, sex, deleted_at)
    SELECT id, role, name, email, phone, password_hash, is_active, created_at, last_login_at, home_address,
           home_lat, home_lng, share_consent_at, dob, sex, deleted_at FROM users;
DROP TABLE users;
ALTER TABLE users_v3 RENAME TO users;

ALTER TABLE doctors ADD COLUMN state TEXT;
ALTER TABLE doctors ADD COLUMN share_insights_at TEXT;
ALTER TABLE doctors ADD COLUMN accept_mr_meetings INTEGER NOT NULL DEFAULT 1;
ALTER TABLE pharmacies ADD COLUMN state TEXT;

CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    cin TEXT,
    gstin TEXT,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT,
    website TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    updated_at TEXT
);

CREATE TABLE divisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    care_areas TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (company_id, name)
);

CREATE TABLE mrs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    division_id INTEGER REFERENCES divisions(id),
    employee_code TEXT,
    territory_city TEXT,
    territory_state TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_mrs_company ON mrs(company_id);

CREATE TABLE mr_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mr_id INTEGER NOT NULL REFERENCES users(id),
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    division_id INTEGER REFERENCES divisions(id),
    purpose TEXT NOT NULL,
    products TEXT,
    preferred_date TEXT,
    preferred_time TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'completed', 'cancelled')),
    doctor_note TEXT,
    scheduled_for TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_mr_requests_doctor ON mr_requests(doctor_id, status);
CREATE INDEX idx_mr_requests_mr ON mr_requests(mr_id);

ALTER TABLE medicines ADD COLUMN care_area TEXT;
ALTER TABLE medicines ADD COLUMN manufacturer TEXT;
ALTER TABLE medicines ADD COLUMN company_id INTEGER REFERENCES companies(id);

CREATE TABLE conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    care_area TEXT NOT NULL
);

CREATE TABLE prescription_conditions (
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    condition_id INTEGER NOT NULL REFERENCES conditions(id),
    PRIMARY KEY (prescription_id, condition_id)
);
CREATE INDEX idx_rx_conditions_condition ON prescription_conditions(condition_id);

ALTER TABLE prescriptions ADD COLUMN parent_id TEXT REFERENCES prescriptions(id);
ALTER TABLE prescriptions ADD COLUMN refill_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prescriptions ADD COLUMN refills_allowed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prescriptions ADD COLUMN refills_used INTEGER NOT NULL DEFAULT 0;
ALTER TABLE prescriptions ADD COLUMN refill_until TEXT;
ALTER TABLE prescriptions ADD COLUMN refill_reminded_at TEXT;
CREATE INDEX idx_rx_created ON prescriptions(created_at);
CREATE INDEX idx_rx_parent ON prescriptions(parent_id);

ALTER TABLE prescription_items ADD COLUMN daily_units REAL;
ALTER TABLE prescription_items ADD COLUMN duration_days INTEGER;
ALTER TABLE prescription_items ADD COLUMN is_long_term INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_rx_items_rx ON prescription_items(prescription_id);

-- One row per medicine handed over: when the patient will run out, for refills and stock forecasts
CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES medicine_orders(id) ON DELETE CASCADE,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
    prescription_item_id INTEGER NOT NULL REFERENCES prescription_items(id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id),
    medicine_id INTEGER REFERENCES medicines(id),
    name TEXT NOT NULL,
    generic_name TEXT,
    strength TEXT,
    units INTEGER NOT NULL,
    daily_units REAL,
    is_long_term INTEGER NOT NULL DEFAULT 0,
    started_on TEXT NOT NULL,
    ends_on TEXT,
    lat REAL,
    lng REAL,
    UNIQUE (order_id, prescription_item_id)
);
CREATE INDEX idx_courses_pharmacy ON courses(pharmacy_id, ends_on);
CREATE INDEX idx_courses_ends ON courses(ends_on);
CREATE INDEX idx_courses_patient ON courses(patient_id);
"""

# Labs (tests from prescriptions, quotes, bookings, reports) and patients' health records
LABS_SCHEMA = """
CREATE TABLE users_v4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK (role IN ('patient', 'doctor', 'admin', 'pharmacy', 'company', 'mr', 'lab')),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    home_address TEXT,
    home_lat REAL,
    home_lng REAL,
    share_consent_at TEXT,
    dob TEXT,
    sex TEXT,
    deleted_at TEXT,
    city TEXT,
    state TEXT
);
INSERT INTO users_v4 SELECT id, role, name, email, phone, password_hash, is_active, created_at, last_login_at, home_address,
    home_lat, home_lng, share_consent_at, dob, sex, deleted_at, city, state FROM users;
DROP TABLE users;
ALTER TABLE users_v4 RENAME TO users;

CREATE TABLE app_secrets (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE labs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    lab_name TEXT NOT NULL,
    registration_no TEXT NOT NULL,
    nabl_no TEXT,
    pathologist_name TEXT NOT NULL,
    pathologist_reg_no TEXT NOT NULL,
    gstin TEXT,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT,
    pincode TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    opening_hours TEXT,
    home_collection INTEGER NOT NULL DEFAULT 0,
    collection_fee_paise INTEGER NOT NULL DEFAULT 0,
    collection_radius_km REAL NOT NULL DEFAULT 3,
    flat_discount_percent REAL,
    report_hours INTEGER NOT NULL DEFAULT 24,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    status_reason TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    updated_at TEXT
);
CREATE INDEX idx_labs_geo ON labs(status, lat, lng);

CREATE TABLE lab_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT,
    category TEXT NOT NULL DEFAULT 'Blood',
    sample_type TEXT,
    fasting INTEGER NOT NULL DEFAULT 0,
    mrp_paise INTEGER,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_lab_tests_name ON lab_tests(lower(name));

CREATE TABLE lab_prices (
    lab_id INTEGER NOT NULL REFERENCES labs(user_id) ON DELETE CASCADE,
    test_id INTEGER NOT NULL REFERENCES lab_tests(id),
    mrp_paise INTEGER NOT NULL,
    price_paise INTEGER NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    report_hours INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lab_id, test_id)
);

CREATE TABLE prescription_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    test_id INTEGER REFERENCES lab_tests(id),
    name TEXT NOT NULL,
    instructions TEXT
);
CREATE INDEX idx_rx_tests ON prescription_tests(prescription_id);

CREATE TABLE lab_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    lab_id INTEGER NOT NULL REFERENCES labs(user_id),
    distance_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'quoted', 'declined', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (prescription_id, lab_id)
);
CREATE INDEX idx_lab_requests_lab ON lab_requests(lab_id, status);

CREATE TABLE lab_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    lab_id INTEGER NOT NULL REFERENCES labs(user_id),
    subtotal_paise INTEGER NOT NULL,
    home_collection INTEGER NOT NULL DEFAULT 0,
    collection_fee_paise INTEGER NOT NULL DEFAULT 0,
    report_hours INTEGER,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn', 'accepted', 'lost')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX idx_lab_quotes_rx ON lab_quotes(prescription_id, status);

CREATE TABLE lab_quote_items (
    quote_id INTEGER NOT NULL REFERENCES lab_quotes(id) ON DELETE CASCADE,
    prescription_test_id INTEGER NOT NULL REFERENCES prescription_tests(id),
    available INTEGER NOT NULL,
    price_paise INTEGER NOT NULL DEFAULT 0,
    mrp_paise INTEGER
);

CREATE TABLE lab_bookings (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
    lab_id INTEGER NOT NULL REFERENCES labs(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    quote_id INTEGER REFERENCES lab_quotes(id),
    collection TEXT NOT NULL CHECK (collection IN ('visit', 'home')),
    preferred_date TEXT,
    preferred_time TEXT,
    address TEXT,
    distance_km REAL,
    subtotal_paise INTEGER NOT NULL,
    collection_fee_paise INTEGER NOT NULL DEFAULT 0,
    total_paise INTEGER NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'lab' CHECK (payment_method IN ('lab')),
    payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'paid')),
    status TEXT NOT NULL CHECK (status IN ('booked', 'accepted', 'sample_collected', 'report_ready', 'completed',
                                           'rejected', 'cancelled')),
    status_reason TEXT,
    reminded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_lab_bookings_lab ON lab_bookings(lab_id, status);
CREATE INDEX idx_lab_bookings_patient ON lab_bookings(patient_id);

CREATE TABLE lab_booking_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id TEXT NOT NULL REFERENCES lab_bookings(id) ON DELETE CASCADE,
    prescription_test_id INTEGER NOT NULL REFERENCES prescription_tests(id),
    test_id INTEGER REFERENCES lab_tests(id),
    name TEXT NOT NULL,
    price_paise INTEGER NOT NULL
);

CREATE TABLE health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('lab_report', 'other_report', 'prescription_copy', 'medicine_bill', 'other')),
    title TEXT NOT NULL,
    record_date TEXT NOT NULL,
    file_path TEXT,
    file_name TEXT,
    content_type TEXT,
    size_bytes INTEGER,
    uploaded_by INTEGER REFERENCES users(id),
    uploader_role TEXT NOT NULL,
    lab_booking_id TEXT REFERENCES lab_bookings(id),
    lab_id INTEGER REFERENCES labs(user_id),
    doctor_id INTEGER REFERENCES doctors(user_id),
    share_with_doctors INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX idx_records_patient ON health_records(patient_id, record_date);
CREATE INDEX idx_records_booking ON health_records(lab_booking_id);

CREATE TABLE record_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES health_records(id) ON DELETE CASCADE,
    test_id INTEGER REFERENCES lab_tests(id),
    name TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    flag TEXT CHECK (flag IN ('low', 'normal', 'high'))
);
CREATE INDEX idx_record_values ON record_values(record_id);

CREATE TABLE patient_medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    strength TEXT,
    dosage TEXT,
    since TEXT,
    prescribed_by TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_patient_medicines ON patient_medicines(patient_id);
"""

# Patient-uploaded prescriptions (from any doctor): nearby pharmacies and labs read them and send quotations
UPLOADS_SCHEMA = """
CREATE TABLE rx_uploads (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    record_id INTEGER NOT NULL REFERENCES health_records(id),
    want_medicines INTEGER NOT NULL DEFAULT 1,
    want_tests INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    radius_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX idx_rx_uploads_patient ON rx_uploads(patient_id);

CREATE TABLE upload_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id TEXT NOT NULL REFERENCES rx_uploads(id) ON DELETE CASCADE,
    partner_role TEXT NOT NULL CHECK (partner_role IN ('pharmacy', 'lab')),
    partner_id INTEGER NOT NULL REFERENCES users(id),
    distance_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'quoted', 'declined', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (upload_id, partner_id)
);
CREATE INDEX idx_upload_requests_partner ON upload_requests(partner_id, status);

CREATE TABLE upload_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id TEXT NOT NULL REFERENCES rx_uploads(id) ON DELETE CASCADE,
    partner_role TEXT NOT NULL CHECK (partner_role IN ('pharmacy', 'lab')),
    partner_id INTEGER NOT NULL REFERENCES users(id),
    subtotal_paise INTEGER NOT NULL,
    mrp_total_paise INTEGER NOT NULL,
    lines_total INTEGER NOT NULL,
    lines_available INTEGER NOT NULL,
    delivery_available INTEGER NOT NULL DEFAULT 0,
    delivery_fee_paise INTEGER NOT NULL DEFAULT 0,
    ready_minutes INTEGER,
    delivery_minutes INTEGER,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn', 'accepted', 'lost')),
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX idx_upload_quotes ON upload_quotes(upload_id, status);

CREATE TABLE upload_quote_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL REFERENCES upload_quotes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    unit_price_paise INTEGER NOT NULL DEFAULT 0,
    mrp_paise INTEGER
);

CREATE TABLE upload_orders (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    upload_id TEXT NOT NULL REFERENCES rx_uploads(id),
    quote_id INTEGER NOT NULL REFERENCES upload_quotes(id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    partner_role TEXT NOT NULL CHECK (partner_role IN ('pharmacy', 'lab')),
    partner_id INTEGER NOT NULL REFERENCES users(id),
    fulfilment TEXT NOT NULL CHECK (fulfilment IN ('pickup', 'delivery', 'visit', 'home')),
    address TEXT,
    preferred_date TEXT,
    preferred_time TEXT,
    subtotal_paise INTEGER NOT NULL,
    fee_paise INTEGER NOT NULL DEFAULT 0,
    total_paise INTEGER NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'paid')),
    status TEXT NOT NULL CHECK (status IN ('placed', 'accepted', 'ready', 'sample_collected', 'report_ready',
                                           'completed', 'rejected', 'cancelled')),
    status_reason TEXT,
    handover_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_upload_orders_partner ON upload_orders(partner_id, status);
CREATE INDEX idx_upload_orders_patient ON upload_orders(patient_id);

ALTER TABLE health_records ADD COLUMN upload_order_id TEXT REFERENCES upload_orders(id);
"""

# Telling partners the patient chose someone else, and patient <-> pharmacy / lab chats about an order
CHATS_SCHEMA = """
ALTER TABLE upload_requests ADD COLUMN outcome TEXT;
ALTER TABLE rx_requests ADD COLUMN outcome TEXT;
ALTER TABLE lab_requests ADD COLUMN outcome TEXT;

CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    partner_role TEXT NOT NULL CHECK (partner_role IN ('pharmacy', 'lab')),
    partner_id INTEGER NOT NULL REFERENCES users(id),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('upload_order', 'medicine_order', 'lab_booking')),
    subject_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    closed_by_role TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    last_message_at TEXT NOT NULL,
    patient_read_at TEXT,
    partner_read_at TEXT,
    UNIQUE (subject_kind, subject_id)
);
CREATE INDEX idx_conversations_patient ON conversations(patient_id, last_message_at);
CREATE INDEX idx_conversations_partner ON conversations(partner_id, last_message_at);

CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    author_role TEXT NOT NULL CHECK (author_role IN ('patient', 'pharmacy', 'lab', 'system')),
    author_id INTEGER REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_conversation_messages ON conversation_messages(conversation_id, id);
"""

# Home visits: the patient asks, the doctor sends charges and time, the patient accepts
HOME_VISITS_SCHEMA = """
CREATE TABLE appointments_v2 (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    service_id INTEGER REFERENCES services(id),
    service_name TEXT NOT NULL,
    duration INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'requested', 'awaiting_patient', 'confirmed', 'completed',
                                           'no_show', 'cancelled', 'expired')),
    reason TEXT,
    doctor_notes TEXT,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('clinic', 'online')),
    payment_status TEXT NOT NULL CHECK (payment_status IN
        ('unpaid', 'pending', 'paid', 'refunded', 'refund_failed', 'not_refundable')),
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    refund_id TEXT,
    cancelled_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reminder_sent_at TEXT,
    visit_type TEXT NOT NULL DEFAULT 'clinic' CHECK (visit_type IN ('clinic', 'home')),
    visit_address TEXT,
    visit_lat REAL,
    visit_lng REAL,
    travel_fee INTEGER NOT NULL DEFAULT 0,
    visit_note TEXT,
    quoted_at TEXT
);
INSERT INTO appointments_v2 (id, reference, doctor_id, patient_id, service_id, service_name, duration, fee, appointment_date, appointment_time, status, reason, doctor_notes, payment_method, payment_status, razorpay_order_id, razorpay_payment_id, refund_id, cancelled_by, created_at, updated_at, reminder_sent_at) SELECT id, reference, doctor_id, patient_id, service_id, service_name, duration, fee, appointment_date, appointment_time, status, reason, doctor_notes, payment_method, payment_status, razorpay_order_id, razorpay_payment_id, refund_id, cancelled_by, created_at, updated_at, reminder_sent_at FROM appointments;
DROP TABLE appointments;
ALTER TABLE appointments_v2 RENAME TO appointments;
CREATE INDEX idx_appt_doctor_date ON appointments(doctor_id, appointment_date);
CREATE INDEX idx_appt_patient ON appointments(patient_id, appointment_date);
CREATE INDEX idx_appt_order ON appointments(razorpay_order_id);

ALTER TABLE doctors ADD COLUMN offers_home_visits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE doctors ADD COLUMN home_visit_radius_km REAL NOT NULL DEFAULT 5;
ALTER TABLE doctors ADD COLUMN home_visit_fee INTEGER;
"""

# In-app notifications, family members, health profile, medicine reminders, follow-ups and video consults
ADVANCED_SCHEMA = """
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'update',
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX idx_notifications_user ON notifications(user_id, read_at, id);

CREATE TABLE family_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    relation TEXT NOT NULL,
    dob TEXT,
    sex TEXT,
    blood_group TEXT,
    allergies TEXT,
    conditions TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX idx_family_patient ON family_members(patient_id);

CREATE TABLE dose_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES users(id),
    source TEXT NOT NULL CHECK (source IN ('course', 'rx', 'own')),
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    dose_date TEXT NOT NULL,
    slot TEXT NOT NULL CHECK (slot IN ('morning', 'afternoon', 'evening', 'night')),
    status TEXT NOT NULL CHECK (status IN ('taken', 'skipped')),
    logged_at TEXT NOT NULL,
    UNIQUE (patient_id, source, source_id, dose_date, slot)
);
CREATE INDEX idx_dose_logs ON dose_logs(patient_id, dose_date);

ALTER TABLE users ADD COLUMN blood_group TEXT;
ALTER TABLE users ADD COLUMN allergies TEXT;
ALTER TABLE users ADD COLUMN chronic_conditions TEXT;
ALTER TABLE users ADD COLUMN emergency_name TEXT;
ALTER TABLE users ADD COLUMN emergency_phone TEXT;
ALTER TABLE users ADD COLUMN remind_times TEXT;
ALTER TABLE users ADD COLUMN dose_email_digest INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN dose_digest_on TEXT;

ALTER TABLE prescriptions ADD COLUMN family_member_id INTEGER REFERENCES family_members(id);
ALTER TABLE prescriptions ADD COLUMN follow_up_on TEXT;
ALTER TABLE prescriptions ADD COLUMN follow_up_reminded_at TEXT;
ALTER TABLE health_records ADD COLUMN family_member_id INTEGER REFERENCES family_members(id);

ALTER TABLE doctors ADD COLUMN offers_video INTEGER NOT NULL DEFAULT 0;
ALTER TABLE doctors ADD COLUMN video_fee INTEGER;

CREATE TABLE appointments_v3 (
    id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    doctor_id INTEGER NOT NULL REFERENCES doctors(user_id),
    patient_id INTEGER NOT NULL REFERENCES users(id),
    service_id INTEGER REFERENCES services(id),
    service_name TEXT NOT NULL,
    duration INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'requested', 'awaiting_patient', 'confirmed', 'completed',
                                           'no_show', 'cancelled', 'expired')),
    reason TEXT,
    doctor_notes TEXT,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('clinic', 'online')),
    payment_status TEXT NOT NULL CHECK (payment_status IN
        ('unpaid', 'pending', 'paid', 'refunded', 'refund_failed', 'not_refundable')),
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    refund_id TEXT,
    cancelled_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reminder_sent_at TEXT,
    visit_type TEXT NOT NULL DEFAULT 'clinic' CHECK (visit_type IN ('clinic', 'home', 'video')),
    visit_address TEXT,
    visit_lat REAL,
    visit_lng REAL,
    travel_fee INTEGER NOT NULL DEFAULT 0,
    visit_note TEXT,
    quoted_at TEXT,
    family_member_id INTEGER REFERENCES family_members(id),
    video_room TEXT
);
INSERT INTO appointments_v3 (id, reference, doctor_id, patient_id, service_id, service_name, duration, fee, appointment_date, appointment_time, status, reason, doctor_notes, payment_method, payment_status, razorpay_order_id, razorpay_payment_id, refund_id, cancelled_by, created_at, updated_at, reminder_sent_at, visit_type, visit_address, visit_lat, visit_lng, travel_fee, visit_note, quoted_at) SELECT id, reference, doctor_id, patient_id, service_id, service_name, duration, fee, appointment_date, appointment_time, status, reason, doctor_notes, payment_method, payment_status, razorpay_order_id, razorpay_payment_id, refund_id, cancelled_by, created_at, updated_at, reminder_sent_at, visit_type, visit_address, visit_lat, visit_lng, travel_fee, visit_note, quoted_at FROM appointments;
DROP TABLE appointments;
ALTER TABLE appointments_v3 RENAME TO appointments;
CREATE INDEX idx_appt_doctor_date ON appointments(doctor_id, appointment_date);
CREATE INDEX idx_appt_patient ON appointments(patient_id, appointment_date);
CREATE INDEX idx_appt_order ON appointments(razorpay_order_id);
"""

# Intelligence: salt matching, prescription safety, AI reading of uploads and reports, risk scores, alerts
INTELLIGENCE_SCHEMA = """
ALTER TABLE medicines ADD COLUMN salt_key TEXT;
ALTER TABLE medicines ADD COLUMN strength_key TEXT;
CREATE INDEX idx_medicines_salt ON medicines(salt_key, strength_key);
ALTER TABLE patient_medicines ADD COLUMN generic_name TEXT;
ALTER TABLE patient_medicines ADD COLUMN medicine_id INTEGER REFERENCES medicines(id);
ALTER TABLE prescriptions ADD COLUMN pregnancy TEXT;
ALTER TABLE prescriptions ADD COLUMN safety_alerts TEXT;
ALTER TABLE prescriptions ADD COLUMN safety_ack_at TEXT;
ALTER TABLE rx_uploads ADD COLUMN ai_read_consent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE rx_uploads ADD COLUMN typed_lines TEXT;
ALTER TABLE upload_quote_lines ADD COLUMN medicine_id INTEGER REFERENCES medicines(id);
ALTER TABLE appointments ADD COLUMN no_show_risk INTEGER;
ALTER TABLE appointments ADD COLUMN risk_nudged_at TEXT;
ALTER TABLE appointments ADD COLUMN attendance_confirmed_at TEXT;
ALTER TABLE pharmacy_stock ADD COLUMN on_hand INTEGER;

CREATE TABLE ai_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('rx_upload', 'lab_report')),
    source_id TEXT NOT NULL,
    record_id INTEGER REFERENCES health_records(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    engine TEXT,
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, source_id)
);
CREATE INDEX idx_ai_extractions_status ON ai_extractions(status);

CREATE TABLE ai_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability TEXT NOT NULL,
    user_id INTEGER,
    model TEXT,
    ok INTEGER NOT NULL,
    ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ai_calls_created ON ai_calls(created_at);

CREATE TABLE intel_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    period TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (kind, subject_id, period)
);
"""

# Each entry upgrades the database by one version.
MIGRATIONS = [NETWORK_SCHEMA, OFFERS_SCHEMA, TRUST_SCHEMA, PHARMA_SCHEMA, LABS_SCHEMA, UPLOADS_SCHEMA, CHATS_SCHEMA,
              HOME_VISITS_SCHEMA, ADVANCED_SCHEMA, INTELLIGENCE_SCHEMA]


def _migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, script in enumerate(MIGRATIONS[version:], start=version + 1):
        # Rebuilding a table needs foreign keys off (outside the transaction)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _statements(script):
                conn.execute(statement)
            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Migration {target} broke foreign keys: {problems[:3]}")
            conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def _statements(script: str):
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""


def init_db():
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        # The single-clinic version used differently shaped tables. Keep its rows for
        # reference under a legacy name instead of mixing them into the platform.
        for table in ("appointments", "blocked_times"):
            cols = _columns(conn, table)
            if cols and "doctor_id" not in cols:
                conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}_v1")
        conn.executescript(SCHEMA)
        _migrate(conn)
        _seed_reference(conn)
        if "salt_key" in _columns(conn, "medicines"):
            from shared import salts
            salts.backfill(conn)


def _seed_reference(conn):
    """Keep the condition list current and fill in states for records saved before states were collected."""
    from shared import reference_data as ref
    if not _columns(conn, "conditions"):
        return  # older schema (only while testing upgrades)
    conn.executemany("INSERT OR IGNORE INTO conditions (code, name, care_area) VALUES (?, ?, ?)", ref.CONDITIONS)
    if _columns(conn, "lab_tests"):
        conn.executemany("INSERT OR IGNORE INTO lab_tests (name, code, category, sample_type, fasting, mrp_paise, unit, "
                         "ref_low, ref_high, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                         [(n, c, cat, smp, f, mrp * 100, u, lo, hi) for n, c, cat, smp, f, mrp, u, lo, hi in ref.LAB_TESTS])
    for table in ("doctors", "pharmacies") + (("labs",) if _columns(conn, "labs") else ()):
        for row in conn.execute(f"SELECT user_id, city FROM {table} WHERE state IS NULL").fetchall():
            state = ref.state_for_city(row["city"])
            if state:
                conn.execute(f"UPDATE {table} SET state = ? WHERE user_id = ?", (state, row["user_id"]))
