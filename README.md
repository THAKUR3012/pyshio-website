# Pyshio: Nearby Doctors & Medicines Network

A location-based health network that connects **patients, doctors and pharmacies within 3 km of the patient's home**:

1. Patients see verified doctors near them, **cheapest fee first**, and book.
2. After the visit, the doctor issues a **digital prescription**.
3. The prescription goes automatically to **licensed pharmacies within 3 km** of the patient. Pharmacies see the medicines and distance, never the patient's identity.
4. Every nearby pharmacy is notified (email + live alert on its dashboard). The patient sees three highlights, **cheapest price**, **best discount** (% off MRP) and **fastest delivery**, and can sort by each. Prices come from live quotes, the pharmacy's price list, or its flat "% off MRP" (same-salt generic substitutes if the doctor allows).
5. The patient orders for pickup or delivery and pays online or at the pharmacy. The pharmacy fulfils it; the doctor sees it was dispensed.
6. Long-term medicines can carry **doctor-approved refills**: Pyshio reminds the patient before they run out, and one tap sends the refill to nearby pharmacies. Pharmacies get a **demand forecast** and see when their customers' courses end.
7. When the doctor suggests **tests**, they go to verified **labs within 3 km**. The patient sees the **cheapest**, **fastest report** and **home collection** options, books one, and the lab uploads the **report with key results**. Everything lands in the patient's **Health records** (reports, trends such as HbA1c over time, their own files, other medicines).
8. **Pharma companies** see **sales and demand for their products**: units prescribed and sold, sales ₹, share against other brands of the same salt, **which diseases** they are prescribed for, and **doctor by doctor** (doctors who opt in by name, all others under an anonymous code). Their **MRs** see the same for their territory and division, and request meetings. The super admin sees **analytics by disease, age, city, state, medicine and lab test**.

**Stack:** FastAPI (Python) · SQLite · Tailwind pages · OpenStreetMap (Leaflet) · optional Razorpay · SMTP email. Runs on GoDaddy cPanel shared hosting.

## Who can do what

| **Sr No** | **Role** | **Pages** | **Can** |
|---:|:---|:---|:---|
| 1 | Patient | `/`, `/doctors/<slug>`, `/account` | Set home on a map; find doctors within 3 km (cheapest / nearest) with **"Visited before"** marks; book, pay, reschedule, cancel, message; **History**: every visit and revisit, prescription, order, payment and refund across all doctors and pharmacies (CSV download), doctors seen with **Book again**, and when each medicine runs out; compare medicine prices nearby; order medicines; **refill** long-term medicines |
| 2 | Doctor | `/doctor` | Register with type and clinic pin (admin approves); hours, services, fees, time off; own appointments and patients; private notes; **write prescriptions** with a coded **diagnosis list (ICD-10)**, catalogue search, generic substitution, long-term medicines and refills; **Requests** inbox: new bookings, patient messages, refills used and **MR meeting requests** (accept with a time, decline, mark met); choose whether to accept MR meetings and whether to share prescribing insights; earnings |
| 3 | Pharmacy | `/pharmacy` | Register with drug licence and store pin (admin approves); price list (manual or CSV); receive nearby prescriptions and **quote**; accept / ready / deliver orders; **Forecast** of demand near the store (7 / 30 days, "stock up" flags); **Courses & refills**: which customers' medicines run out soon; earnings |
| 4 | Lab | `/lab` | Register with registration / NABL and a map pin (admin approves); test price list; receive nearby test requests and **quote**; accept, collect samples (walk-in or home), **upload reports** with key results |
| 5 | Pharma company | `/company` | Register (admin approves); divisions with care areas; invite and manage MRs; list own brands; **sales and demand** by product, disease, city and **doctor** (named if the doctor opted in, otherwise anonymous); log of all MR meetings |
| 6 | Medical representative (MR) | `/mr` | Invited by their company; the same **sales & doctor-wise** view for their territory and division; doctors in their territory; request and track meetings |
| 7 | Super admin | `/admin` | **Analytics** by disease, age band and sex, city, state, medicine and lab test, with filters, charts and CSV; approve doctors, pharmacies, **labs** and **pharma companies**; lab test catalogue; all appointments and medicine orders; medicine catalogue; **settlements**; retry refunds; users; activity log |

**Home visits:** doctors can switch on home visits (Profile → I do home visits, with a radius and usual travel charge). Patients filter search by "Home visit available" and choose Clinic visit or Home visit when booking. A home visit is a request: the doctor sends the visit fee, travel charge, final date, time, duration and a note (Requests → Home-visit requests); the visit is confirmed only when the patient accepts. Either side can decline; requests nobody finalises expire at the visit time.

**Upload a prescription:** a patient can photograph a prescription from any doctor (Patient → Upload prescription). It goes automatically to verified pharmacies (medicines) and labs (tests) within 3 km; they open it, quote line by line, and the patient sees quotations sorted by cheapest, best discount, fastest and nearest, then books pickup / delivery or a lab visit / home collection. They can widen to 5 or 10 km. Pharmacies hand over against a 4-digit code; labs upload the report into Health records.

**After the patient chooses:** the request closes for every other pharmacy or lab; those who had quoted get an email, and all see it under "Patients' decisions" on their dashboard. A chat between the patient and the chosen pharmacy or lab opens automatically (chat button → Chats), with order updates posted into it. Either side can close or reopen the chat; nobody else can read it.

**Assistant:** every dashboard has a chat button (bottom right). Each person can ask about their own data only, e.g. a patient "where are my medicines cheapest?" or "dawai sasta kahan milega?", a doctor "who am I seeing today?", a pharmacy "what should I stock up on?", an MR "which doctors prescribe us most?", the admin "what's waiting for approval?". It works out of the box; add `ANTHROPIC_API_KEY` in `.env` to let Claude answer free-form questions using the same role-limited data.

**Intelligence (works without any AI key; Claude makes some of it smarter):**

| **Sr No** | **Feature** | **Who** | **What it does** |
|---:|:---|:---|:---|
| 1 | Prescription safety check | Doctor | While writing, checks drug–drug interactions (curated list), the same salt twice (also inside combinations), clashes with what the patient already takes (courses, own list, other doctors' prescriptions), allergies, age (children, 65+), pregnancy / breastfeeding and the daily dose ceiling. **Major** alerts must be acknowledged before issuing; the acknowledgement is stored. Rules only, never AI |
| 2 | AI scribe | Doctor | Type or dictate the consultation notes (“Dx: T2DM · Tab Glycomet 500 1-0-1 x 30 days · Inv: HbA1c · review 1 month”); the prescription form fills itself (catalogue matches, diagnoses, tests, quantity, follow-up). Nothing invented: every name is matched to the catalogue, the rest is listed as not understood. The doctor reviews and issues |
| 3 | Prescription photo reading | Patient → pharmacy / lab | With the patient's consent, an uploaded photo is read (Claude vision) or the patient types the names; pharmacies and labs get **pre-filled, pre-priced quote lines** (same-salt substitutes from their own list) to check; the patient sees what was read with allergy and interaction warnings |
| 4 | Symptom checker | Everyone | “Kamar mein dard”, “child has fever”: emergency signs first (chest pain, stroke signs, breathing, bleeding, suicidal thoughts → call 108 / Tele-MANAS 14416), otherwise the right speciality and the cheapest such doctors within 3 km. Symptom text is not stored |
| 5 | Lab report reader and explainer | Lab, patient, doctor | Labs get the values read from the report file to confirm (flags always recomputed from the ranges; unit mismatches marked). Patients get each result explained in plain English or Hinglish, with the trend (↑ / ↓, toward or away from normal) |
| 6 | Smarter stock forecast | Pharmacy | 8 weeks of nearby prescribing with the trend and busiest weekdays, refills due, a safety buffer and the units on the shelf → how many packs to reorder |
| 7 | Risk nudges | Patient, doctor | A transparent no-show score: the doctor sees “may need a reminder call” (with reasons); high-risk patients get one “Still coming?” message 3–6 hours before. A gentle weekly note when someone's medicine-taking drops sharply |
| 8 | MR next-best-action and alerts | MR, pharma company | Who to visit next and why (speciality fit, visit history, and the salt-market share gap **only for doctors who share their insights by name**: anonymous doctors are never ranked on prescribing). Visit briefs. Weekly alerts when a product's prescribing rises or falls unusually or its share of the salt drops |
| 9 | Smarter assistant | Everyone | Tools take arguments (“how is my HbA1c?”, “forecast for 30 days”), new skills (symptoms, explain my report, adherence, visits at risk, reorder, who to visit, alerts), suggested follow-up questions, prompt caching, and emergency signs answered before anything else |

**Everyday care:**
- **Notification bell** on every page for every role: each update (booking, quote, report, order, approval…) appears there as well as by email, with a device pop-up while the app is open.
- **Install as an app** (Android / desktop Chrome "Install app" button, iPhone "Add to Home Screen"), with an offline page.
- **Family:** patients add family members (relation, age, blood group, allergies) and book doctors, keep reports and get prescriptions for them; doctors see the family member's own details.
- **Health profile:** blood group, allergies, long-term conditions and an emergency contact; doctors see it before the visit and get a warning while prescribing a medicine the patient is allergic to.
- **Today's medicines:** from prescriptions, purchases and the patient's own list, by time of day, with Taken / Skip, a 30-day adherence score (also visible to their doctors), reminder times, an optional morning email and bell reminders.
- **Follow-ups:** the doctor sets a follow-up date on the prescription; the patient is reminded two days before and books in one tap.
- **Video consultations:** doctors switch them on (Profile); patients filter by "Video consult", book any slot as a video call and both join a private room from the appointment, 10 minutes before the start.

Prescriptions print from `/prescriptions/<id>` (doctor, qualification and registration number included).

## Run locally

```bash
pip install -r requirements-dev.txt

# PowerShell
$env:PYSHIO_ADMIN_EMAIL = "admin@example.com"; $env:PYSHIO_ADMIN_PASSWORD = "choose-a-password"
# bash
export PYSHIO_ADMIN_EMAIL=admin@example.com PYSHIO_ADMIN_PASSWORD=choose-a-password

python -m uvicorn server:app --port 8080
```

Open http://localhost:8080. Settings can also go in a `.env` file (see [.env.example](.env.example)).

## Demo data (testing only)

```bash
python seed_demo.py          # fictional doctors, pharmacies, patients with 6 months of history, 3 pharma companies, 12 MRs
python seed_demo.py --wipe   # remove all of it
```

Everything is labelled "(Demo)", uses `@pyshio-demo.invalid` emails (never emailed) and the password `demo-pass-123`
(e.g. `patient1@pyshio-demo.invalid`, `history001@…` (a patient with history), `doctor01@…`, `pharmacy01@…`,
`lab01@…`, `company1@…`, `mr01@…`). The script is left out of the GoDaddy upload
package on purpose. Don't run it on the live site.

## Tests

```bash
python -m pytest
```

254 tests. Each uses its own temporary database. Razorpay, email and Claude are faked (every AI feature is tested without a key, with a scripted Claude, and with Claude down).
They cover the 3 km matching, cheapest-first ranking, generic substitutes, quotes, partial and delivery orders, refunds, settlements, privacy between all roles, patient history and revisits, refills and course tracking, demand forecasts, MR consent and company isolation, analytics with small-group suppression, notifications, family members, health profiles and allergy checks, medicine reminders and adherence, follow-ups, video consultations, the installable app, the database upgrades, and the intelligence features: prescription safety rules, the scribe, photo and report reading, the symptom checker's emergency signs, forecasts, risk nudges, MR next-best-action privacy and the assistant's tools.

## Code layout: vertical slices

Each feature lives in its own folder under `features/` and owns its whole stack (endpoints, logic and queries,
emails, scheduled jobs and its browser script). Shared infrastructure is in `shared/` (including `ai.py`, the one Claude client every slice uses, `salts.py` for salt
matching, and the curated rule lists `drug_rules.py`, `triage_rules.py`, `lab_explain.py`); pages are in `web/`.

```
server.py            puts the slices together (routes, static files, errors, start-up)
jobs.py              runs every slice's scheduled job (cron, every 5 min)
shared/              settings, db (schema + upgrades), auth, mailer, payments (Razorpay), geo, validators, common
features/<slice>/
    api.py           HTTP endpoints + request models (exposes `router`)
    logic.py         the slice's logic and queries (what other slices may call)
    emails.py        the slice's emails                      (optional)
    jobs.py          scheduled work                           (optional)
    static/*.js      the slice's browser script, served at /features/<slice>/...   (optional)
web/                 one HTML page per stakeholder, plus web/assets/app.js (shared helpers)
tests/               one test file per feature area (HTTP-level, each with its own database)
```

A new feature = a new folder in `features/` plus its name in `features/__init__.py` (`SLICES`, the route order).
Every email a slice sends also appears in the recipient's notification bell automatically.

| **Sr No** | **Slice** | **What it does** |
|---:|:---|:---|
| 1 | `accounts` | Sign-up, login, password reset, profile, home location, consent, data export, account closure |
| 2 | `doctors` | Doctor registration, public directory (cheapest / nearest / home visit / video filters), hours, services, leave, approval |
| 3 | `appointments` | Booking, online payment, cancel / reschedule, messages, receipts, walk-ins, lists, slot scheduling, reminders |
| 4 | `home_visits` | Patient asks, doctor sends charges and time, patient accepts |
| 5 | `video_consults` | Video consultations: doctor setting, private Jitsi room, join window |
| 6 | `family` | Family members; booking, prescriptions and records for them |
| 7 | `health_profile` | Blood group, allergies, conditions, emergency contact; allergy warnings for doctors |
| 8 | `patient_history` | Every visit, prescription, order and payment over time |
| 9 | `prescriptions` | Writing prescriptions (medicines, tests, diagnoses, refills, follow-up date) |
| 10 | `follow_ups` | Follow-up reminders and one-tap booking |
| 11 | `pharmacy_offers` | Prescriptions to nearby pharmacies, quotes, cheapest / discount / fastest comparison |
| 12 | `medicine_orders` | Orders, payment, fulfilment, refunds, invoices |
| 13 | `medicine_courses` | Who takes what until when, refill predictions, pharmacy demand forecast |
| 14 | `medicine_reminders` | Today's doses, taken / skipped log, adherence, reminder times, morning email, bell nudges |
| 15 | `pharmacies` · `medicines` | Pharmacy registration and profile · shared catalogue and price lists |
| 16 | `labs` · `health_records` | Labs, tests and reports · reports, files and other medicines in one place |
| 17 | `prescription_uploads` · `chats` | Upload any prescription for quotes · patient–pharmacy / lab chats |
| 18 | `pharma_insights` · `admin_analytics` | Company / MR sales and demand · super-admin analytics |
| 19 | `settlements` · `reviews` · `online_payments` | Payouts · ratings · Razorpay webhook |
| 20 | `notifications` · `assistant` · `pwa` | Bell for every role · per-role chatbot · installable app (manifest, service worker, icons, offline page) |
| 21 | `rx_safety` · `ai_scribe` | Prescription safety check and issue-time gate · notes to draft prescription |
| 22 | `triage` · `lab_reader` | Symptom checker · lab report reading and plain-language explanations |
| 23 | `risk_nudges` · `pharma_nba` | No-show score, “Still coming?” and adherence notes · MR next-best-action, visit briefs and company alerts |
| 24 | `admin` · `pages` | Users, audit, platform stats, housekeeping · HTML pages, robots.txt, sitemap |

`legacy/` keeps the earlier single-clinic version for reference.

## Deploy

**GoDaddy cPanel:** follow [DEPLOY_GODADDY.md](DEPLOY_GODADDY.md). It includes the legal checklist for prescriptions and e-pharmacy. Build the upload zip with `python build_deploy.py`.
