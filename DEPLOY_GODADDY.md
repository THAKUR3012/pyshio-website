# Deploying Pyshio on GoDaddy (cPanel shared hosting)

Allow about 45–60 minutes. You only need the GoDaddy website and your browser.

## Before you start

| **Sr No** | **What you need** | **Where to find it** |
|---:|:---|:---|
| 1 | GoDaddy **Linux** Web Hosting with **cPanel** | GoDaddy → My Products → Web Hosting → **Manage** → **cPanel Admin** |
| 2 | "**Setup Python App**" in cPanel | cPanel → Software section. If it's missing, ask GoDaddy support to enable it or upgrade the plan |
| 3 | Your domain pointed at this hosting | Usually already done if you bought both from GoDaddy |
| 4 | Upload package `dist/pyshio-godaddy.zip` | Built from this project (see the end of this guide) |
| 5 | *(Optional)* Razorpay business account | Only for online payments. [razorpay.com](https://razorpay.com). KYC can take a few days |

> Windows/Plesk hosting **cannot** run this app. It must be Linux/cPanel.

---

## Step 1: Create the email account that sends notifications

1. cPanel → **Email Accounts** → **+ Create**.
2. Username `bookings` (gives `bookings@yourdomain.com`), set a strong password, then **Create**.
3. Click **Connect Devices** next to it. Note the **Outgoing Server** (usually `mail.yourdomain.com`) and **SMTP Port** (usually `465`).

## Step 2: Upload the files

1. cPanel → **File Manager**. You start in your home folder `/home/<username>`.
2. **+ Folder** → create `pyshio`. **Do not** put it inside `public_html`, or the database and passwords could be downloaded.
3. Open `pyshio` → **Upload** → choose `pyshio-godaddy.zip`. Then right-click the zip → **Extract** → delete the zip.

## Step 3: Fill in your settings

1. File Manager → **Settings** (top right) → tick **Show Hidden Files (dotfiles)**.
2. In `pyshio`, copy `.env.example` → name the copy `.env` → right-click → **Edit**:

| **Sr No** | **Setting** | **What to put** |
|---:|:---|:---|
| 1 | `PYSHIO_SITE_URL` | `https://www.yourdomain.com` |
| 2 | `PYSHIO_TIMEZONE` | `Asia/Kolkata` (GoDaddy servers run on US time) |
| 3 | `PYSHIO_ADMIN_EMAIL` | Your email. This becomes the admin login |
| 4 | `PYSHIO_ADMIN_PASSWORD` | A long password for the admin login |
| 5 | `SMTP_HOST`, `SMTP_PORT` | From Step 1 (`mail.yourdomain.com`, `465`) |
| 6 | `SMTP_USER`, `SMTP_FROM` | `bookings@yourdomain.com` |
| 7 | `SMTP_PASSWORD` | The email account password from Step 1 |
| 8 | `RAZORPAY_*` | Leave blank for now. Step 9 fills these in |
| 9 | `PYSHIO_SUPPORT_EMAIL`, `PYSHIO_SUPPORT_PHONE` | Shown in the footer and as the grievance contact in the privacy policy |

3. **Save Changes**.

## Step 4: Create the Python app

1. cPanel → **Setup Python App** → **Create Application**:

| **Sr No** | **Field** | **Value** |
|---:|:---|:---|
| 1 | Python version | **3.11** (or the highest offered; must be 3.9+) |
| 2 | Application root | `pyshio` |
| 3 | Application URL | your domain, leave the path box empty |
| 4 | Application startup file | `passenger_wsgi.py` |
| 5 | Application Entry point | `application` |

2. **Create**. At the top cPanel shows a command like
   `source /home/<username>/virtualenv/pyshio/3.11/bin/activate && cd /home/<username>/pyshio`.
   **Copy it**, because you'll need it in later steps.

## Step 5: Install the Python packages

On the app page, under **Configuration files**, add `requirements.txt` → **Run Pip Install**.
*(Or cPanel → **Terminal** → paste the Step 4 command → `pip install -r requirements.txt`.)*

## Step 6: Start it and test the whole flow

1. Setup Python App → **Restart**.
2. Open `https://yourdomain.com`. You should see the search page (no doctors yet).
3. **Doctor:** open `/doctor`, register a test physiotherapist. You'll see "under review".
4. **Admin:** open `/login` in another browser (or a private window) and log in with `PYSHIO_ADMIN_EMAIL` / `PYSHIO_ADMIN_PASSWORD`. Under **Doctors**, click **Review** → **Approve**.
5. **Pharmacy:** open `/pharmacy`, register a test pharmacy and pin it on the map within 3 km of the doctor. Approve it in Admin → Pharmacies. In its **Price list** tab, add one medicine (e.g. Dolo 650).
6. **Patient:** create an account at `/login` (tick the sharing consent). In **My health → Profile**, pin your home on the map. On the home page, click **Near me**, open the doctor, then book.
7. **Doctor:** open the appointment → **Write prescription** → pick Dolo 650 from the list → **Issue**.
8. **Pharmacy:** the prescription appears under **Prescriptions** with your price pre-filled → **Send quote**.
9. **Patient:** **My health → Prescriptions** → see the cheapest price → **Order**. **Pharmacy:** Orders → Accept → Ready → Picked up.
10. Check that everyone received the emails listed in Step 7.

If you see an old GoDaddy placeholder page, rename `public_html/index.html` (or `index.php`) to `index-old.html`.

## Step 7: Test email on its own

cPanel → **Terminal** → paste the Step 4 command → then:

```bash
python send_reminders.py --test-email your-own-address@gmail.com
```

If it fails, edit `.env` and try GoDaddy's internal relay: `SMTP_HOST=localhost` and `SMTP_PORT=25`. Then **Restart** and test again.

| **Sr No** | **Event** | **Who gets an email** |
|---:|:---|:---|
| 1 | Doctor registers | Doctor (under review) + all admins (approval needed) |
| 2 | Admin approves / rejects / suspends | Doctor |
| 3 | Patient books | Patient (with calendar invite) + doctor (with the patient's reason) |
| 4 | Reschedule / cancel by anyone | Patient + doctor (with any message and refund details) |
| 5 | Message on an appointment | The other person |
| 6 | ~24 hours before | Patient reminder (needs Step 8) |
| 7 | Doctor issues a prescription | Patient (with cheapest nearby price so far) + every verified pharmacy within 3 km (medicines and distance only) |
| 8 | A pharmacy's quote becomes the cheapest, best discount or fastest delivery | Patient |
| 9 | Patient orders medicines | Pharmacy (with patient name, phone, address) + patient |
| 10 | Pharmacy accepts / ready / out for delivery / rejects | Patient (refund details if it was paid online) |
| 11 | Pharmacy registers / is approved | Pharmacy + all admins |
| 7 | Forgot password | That user |

## Step 8: Schedule the background job

cPanel → **Cron Jobs** → *Add New Cron Job* → Common Settings **Once Per Five Minutes (\*/5 \* \* \* \*)**, command (replace `<username>`, and `3.11` if different):

```
cd /home/<username>/pyshio && /home/<username>/virtualenv/pyshio/3.11/bin/python jobs.py >> jobs.log 2>&1
```

Every run of the job does the following, and each step only acts on what is due:

| **Sr No** | **Task** |
|---:|:---|
| 1 | Releases unpaid booking / order holds after 15 minutes |
| 2 | Reminds a pharmacy about an order waiting for acceptance (10 min), and **auto-rejects + refunds** it at 30 min so the patient can pick the next-best pharmacy (`PYSHIO_ORDER_REMIND_MINUTES` / `PYSHIO_ORDER_ACCEPT_MINUTES`) |
| 3 | Emails appointment reminders ~24 hours ahead and "prescription expiring in 3 days" nudges |
| 4 | Emails "refill due" reminders 3 days before a long-term medicine runs out (only when the doctor allowed refills and some are left) |
| 5 | Clears expired sessions, reset links, old failed-login and rate-limit records |

If you already have the older hourly `send_reminders.py` cron line, it still works (it now runs this same job), but change it to every 5 minutes so the order deadlines are kept.

## Step 9: Online payments with Razorpay (optional)

Skip this step and patients simply pay at the clinic.

1. Razorpay Dashboard → switch to **Test Mode** → **Account & Settings → API Keys** → **Generate Test Key**.
2. Put them in `.env`: `RAZORPAY_KEY_ID=rzp_test_...` and `RAZORPAY_KEY_SECRET=...`
3. Razorpay Dashboard → **Webhooks** → **+ Add New Webhook**:
   - URL: `https://www.yourdomain.com/api/payments/webhook`
   - Secret: type any long random text, then put the same text in `.env` as `RAZORPAY_WEBHOOK_SECRET`
   - Events: tick **payment.captured** and **order.paid**
4. **Restart** the app. Doctors now see a "Pay online" option in their Profile tab; once ticked, patients see it when booking.
5. Make a test booking and pay with Razorpay's [test cards / UPI](https://razorpay.com/docs/payments/payments/test-card-details/). Cancel it more than 24 hours ahead and check the refund appears in the Razorpay dashboard.
6. When it all works: generate **Live Mode** keys, swap them into `.env`, update the webhook in Live Mode, **Restart**.

How payments behave:
- The slot is **held for 15 minutes** while the patient pays. If they don't pay, it's released.
- Patient cancels ≥ 24 hours before → **automatic full refund**. Less than 24 hours → not refundable (change with `PYSHIO_REFUND_CUTOFF_HOURS`).
- Doctor or admin cancels → always a full refund.
- If a refund fails, it shows under **Admin → Failed refunds** with a **Retry refund** button.
- Money goes to **your** Razorpay account. Paying doctors their share is up to you (manually, or later with Razorpay Route).

## Running the nearby network

| **Sr No** | **Task** | **Where** |
|---:|:---|:---|
| 1 | Approve doctors: verify the registration number with the council (NMC / state medical, dental or physiotherapy council) | Admin → Doctors |
| 2 | Approve pharmacies: verify the drug licence (Form 20/21) with the State Drugs Control department, and the pharmacist's registration | Admin → Pharmacies |
| 3 | Keep the medicine catalogue clean, including **MRP per pack** (pharmacies' flat "% off MRP" uses it): correct **generic name, strength and form**, because same generic + strength + form is what makes a cheaper substitute | Admin → Medicines |
| 4 | Pay doctors and pharmacies their online earnings (UPI/NEFT from your bank), then record the payout with the UTR number | Admin → Settlements |
| 5 | Retry any failed refunds | Admin → Appointments / Medicine orders |
| 6 | Approve pharma companies: check the CIN on the MCA portal and their drug manufacturing / marketing licence. Suspending a company cancels its MRs' open meeting requests and blocks their access | Admin → Pharma companies |
| 7 | Watch disease, age, city, state and medicine trends (aggregated; small groups hidden) | Admin → Analytics |
| 8 | Approve labs: check the registration under the Clinical Establishments Act (or state equivalent), the pathologist's registration and the NABL number if given | Admin → Labs |
| 9 | Keep the lab test catalogue tidy: MRP, unit and normal range (used for high/low flags) | Admin → Lab tests |

**Maps:** location search uses OpenStreetMap's free Nominatim service. It allows about 1 search per second, which is fine at launch. For heavy traffic, switch to a paid geocoder (Google Maps, MapmyIndia), a small code change in `web/assets/app.js` (`searchPlaces`).

**Before launch: legal checklist** (this is not legal advice; please review with a lawyer)

| **Sr No** | **Topic** | **What to check** |
|---:|:---|:---|
| 1 | Who may prescribe | Currently **all doctor types** can write medicine prescriptions. In India, allopathic prescribing is limited to registered medical practitioners. Physiotherapists and (outside their own system) AYUSH practitioners generally cannot. Restrict with `PYSHIO_PRESCRIBING_CATEGORIES` in `.env` |
| 2 | Telemedicine Practice Guidelines 2020 | Digital prescriptions must show the doctor's name, qualification and registration number (included), and follow the rules on which drugs may be prescribed after an online-only consultation |
| 3 | Drugs & Cosmetics Act / e-pharmacy rules | Pharmacies must dispense Schedule H/H1 drugs only against a valid prescription, verified by their pharmacist. Schedule X / narcotics should not be ordered online. Check the current e-pharmacy rules for the "marketplace" model |
| 4 | DPDP Act 2023 (health data) | Patients agree at sign-up and can withdraw consent, download their data or close their account from **My health → Profile**. `/privacy` and `/terms` are templates; have a lawyer review them and set your grievance contact (`PYSHIO_SUPPORT_EMAIL`) |
| 5 | Payments | Collecting money for doctors and pharmacies may need Razorpay **Route** (split payments) or a marketplace agreement. Ask Razorpay before going live with online payments |
| 6 | Medical representatives (UCPMP 2024, NMC) | Pharma companies and MRs see doctor-wise prescribing **counts** (never patient data). Doctors who switch on **Show my name to pharma companies** appear by name; all others appear under an anonymous code with only their category and city, and small groups are pooled. **Sharing even anonymous, doctor-level prescribing data with pharma needs a lawyer's review and a clause in the doctor terms**; doctors can change the setting any time (logged in Admin → Activity). Every meeting request and outcome is logged. Companies agree to UCPMP 2024 at sign-up |
| 8 | Labs and health records | Labs must be registered (Clinical Establishments Act / state law); reports are signed by their pathologist. Reports are health data under the DPDP Act: they are stored in the private `uploads/` folder and only the patient, the lab, the ordering doctor and doctors the patient shares with can open them |
| 10 | Uploaded prescriptions | Pharmacies receive the patient's own prescription image (it shows their name) to quote on it. The pharmacist must check the original prescription before handing over prescription-only (Schedule H/H1) medicines, as the app reminds them. Mention this sharing in the privacy policy |
| 9 | AI features (optional) | Without `ANTHROPIC_API_KEY` nothing is sent anywhere and every feature uses built-in rules. With a key, Anthropic receives only what each feature needs: the assistant's answers from the user's own records; prescription photos **only when the patient ticks the consent box**; doctors' notes (names, phones and IDs removed) for the scribe; lab report files for the lab to confirm values; symptom text (not stored); numbers-only report explanations. Add this to the privacy policy and check it under the DPDP Act first. Switch features off one by one with `PYSHIO_AI_<FEATURE>=0` (see `.env.example`). Every call is logged without content in the `ai_calls` table (kept 180 days) |
| 13 | Clinical safety features | The prescription safety check (interactions, duplicate salts, allergies, age, pregnancy, daily dose), the symptom checker's emergency signs and the report explanations are **decision support, not medical advice**: they use a short curated rule list (`shared/drug_rules.py`, `shared/triage_rules.py`, `shared/lab_explain.py`) that a clinician should review before launch and keep up to date. Doctors must acknowledge major alerts before issuing; the acknowledgement is stored on the prescription. AI never decides on safety or emergencies |
| 11 | Family members and health profile | Parents add children, spouses and elderly parents (name, age, blood group, allergies). Their consent (or the guardian's, for children) is needed under the DPDP Act; mention family profiles in the privacy policy |
| 12 | Video consultations | Video calls run on Jitsi Meet (`meet.jit.si`, or your own server via `PYSHIO_VIDEO_BASE_URL`). Only a random room name is shared; the call itself isn't recorded. Follow the Telemedicine Practice Guidelines 2020 (patient consent, doctor identity, what may be prescribed after a video consult). For full control, host your own Jitsi server |
| 7 | Health statistics | Admin analytics use coded diagnoses (ICD-10) in aggregate only, never names or contacts; CSV downloads drop groups under 5. Keep it that way if you share figures outside the company |

## Step 10: HTTPS and backups

Back up **both** `pyshio.db` and the `uploads/` folder (lab reports and patients' files). Never move `uploads/` under `public_html` or `web/`: files must only be served through the app, which checks who is asking. To keep them elsewhere, set `PYSHIO_UPLOAD_DIR` in `.env`.


1. cPanel → **SSL/TLS Status** → **Run AutoSSL**, then cPanel → **Domains** → turn on **Force HTTPS Redirect**.
2. All data lives in `/home/<username>/pyshio/pyshio.db`. Download it regularly (weekly at least) or use cPanel **Backup**.

---

## Updating the site later

Upload changed files into `pyshio` (overwrite), then **Restart**. **Never overwrite `pyshio.db` or `.env`.**

**Upgrading from the version with files such as `network.py` and `index.html` in the top folder:** the code now lives in
three folders: `shared/`, `features/` (one folder per feature) and `web/` (pages and assets).
1. Download a backup of `pyshio.db` and `uploads/` first.
2. Upload and extract the new zip into `pyshio` (it adds the three folders and replaces `server.py`, `jobs.py`,
   `send_reminders.py`, `passenger_wsgi.py`).
3. Delete the old top-level `.py` modules that are now in `shared/` or `features/` (everything except `server.py`,
   `jobs.py`, `send_reminders.py`, `passenger_wsgi.py`), the old top-level `.html` pages and the old `assets/` folder.
4. **Restart**. The database upgrades itself on start (version 9); nothing else changes: same startup file, same cron line.

## Troubleshooting

| **Sr No** | **Symptom** | **Fix** |
|---:|:---|:---|
| 1 | Error / 503 page | Open `pyshio/stderr.log`. The last lines say why |
| 2 | `ModuleNotFoundError` | Step 5 not done. Run pip install, then Restart |
| 3 | Changes don't show | Click **Restart** |
| 4 | Can't stay logged in | `PYSHIO_SITE_URL` starts with `https://` but the site is opened over `http://`. Turn on SSL (Step 10) or fix the URL |
| 5 | Admin login doesn't work | Check `PYSHIO_ADMIN_EMAIL` / `PYSHIO_ADMIN_PASSWORD` in `.env`, then Restart. The admin is created on first start only; if you change the password later, use **Forgot password** on `/login` |
| 6 | Times off by hours | `PYSHIO_TIMEZONE` missing, then Restart |
| 7 | No "Pay online" option | Razorpay keys missing in `.env`, or the doctor hasn't ticked it in their Profile |
| 8 | Paid but booking not confirmed | Check the webhook URL/secret in Step 9. The patient can also open My appointments → Complete payment |
| 9 | Emails not arriving | Step 7 test; check spam; try `localhost` / `25` |

## Building the upload package (on your computer)

```bash
python build_deploy.py
```

Creates `dist/pyshio-godaddy.zip` (`server.py`, `jobs.py`, `passenger_wsgi.py` and the `shared/`, `features/` and `web/` folders). It never includes your local database, `.env`, tests or the `legacy/` folder.
