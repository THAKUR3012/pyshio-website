"""Medicines: the shared catalogue and each pharmacy's price list / stock.

This file: logic and queries.
"""
from __future__ import annotations

from shared import reference_data as ref, salts
from shared.common import paise, stamp
from shared.db import get_db


FORMS = ["tablet", "capsule", "syrup", "suspension", "injection", "drops", "cream", "gel", "ointment",
         "inhaler", "powder", "sachet", "spray", "patch", "other"]


# ---------- Medicine catalogue & price lists ----------
def medicine_view(row) -> dict:
    return {k: row[k] for k in ("id", "name", "generic_name", "strength", "form", "pack_size", "mrp_paise",
                                "care_area", "manufacturer", "company_id")} | {"rx_required": bool(row["rx_required"])}


def is_trusted(conn, user: dict) -> bool:
    """Admins and approved doctors/pharmacies may add to the shared catalogue and set its MRP."""
    if user["role"] == "admin":
        return True
    table = {"doctor": "doctors", "pharmacy": "pharmacies"}.get(user["role"])
    row = conn.execute(f"SELECT status FROM {table} WHERE user_id = ?", (user["id"],)).fetchone() if table else None
    return bool(row and row["status"] == "approved")


UNTRUSTED_CATALOGUE = "Your account must be approved before adding new medicines to the shared catalogue"


def find_or_create_medicine(conn, data: MedicineIn, user_id: int, trusted: bool = True) -> int:
    row = conn.execute("SELECT id FROM medicines WHERE lower(name) = lower(?) AND lower(COALESCE(strength, '')) = "
                       "lower(COALESCE(?, '')) AND lower(COALESCE(form, '')) = lower(COALESCE(?, ''))",
                       (data.name, data.strength, data.form)).fetchone()
    if row:
        if data.mrp and trusted:
            conn.execute("UPDATE medicines SET mrp_paise = ? WHERE id = ? AND mrp_paise IS NULL", (paise(data.mrp), row["id"]))
        return row["id"]
    if not trusted:
        raise ValueError(UNTRUSTED_CATALOGUE)
    cur = conn.execute("INSERT INTO medicines (name, generic_name, strength, form, pack_size, rx_required, created_by, "
                       "created_at, mrp_paise, care_area, manufacturer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (data.name, data.generic_name, data.strength, data.form, data.pack_size,
                        int(data.rx_required), user_id, stamp(), paise(data.mrp) if data.mrp else None,
                        data.care_area or ref.care_area_for(data.generic_name), data.manufacturer))
    salts.set_keys(conn, cur.lastrowid)
    return cur.lastrowid


STOCK_SELECT = """SELECT s.*, m.name, m.generic_name, m.strength, m.form, m.pack_size, m.rx_required
                  FROM pharmacy_stock s JOIN medicines m ON m.id = s.medicine_id"""


def stock_view(row) -> dict:
    return {"medicine_id": row["medicine_id"], "name": row["name"], "generic_name": row["generic_name"],
            "strength": row["strength"], "form": row["form"], "pack_size": row["pack_size"],
            "mrp_paise": row["mrp_paise"], "price_paise": row["price_paise"], "in_stock": bool(row["in_stock"]),
            "on_hand": row["on_hand"] if "on_hand" in row.keys() else None, "updated_at": row["updated_at"]}


def upsert_stock(conn, pharmacy_id: int, medicine_id: int, mrp: float, price: float, in_stock: bool,
                 trusted: bool = True) -> None:
    conn.execute("INSERT INTO pharmacy_stock (pharmacy_id, medicine_id, mrp_paise, price_paise, in_stock, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (pharmacy_id, medicine_id) DO UPDATE SET "
                 "mrp_paise = excluded.mrp_paise, price_paise = excluded.price_paise, in_stock = excluded.in_stock, "
                 "updated_at = excluded.updated_at",
                 (pharmacy_id, medicine_id, paise(mrp), paise(price), int(in_stock), stamp()))
    # The first MRP an approved pharmacy gives becomes the catalogue MRP (admins can correct it)
    if trusted:
        conn.execute("UPDATE medicines SET mrp_paise = ? WHERE id = ? AND mrp_paise IS NULL", (paise(mrp), medicine_id))


CSV_COLUMNS = ["name", "generic_name", "strength", "form", "pack_size", "mrp", "price", "in_stock"]


# ---------- Starter catalogue ----------
# Common Indian brands with their generic (salt) so price comparison can offer same-salt substitutes.
# Prices are NOT seeded: every pharmacy sets its own. Review/extend in Admin → Medicines before launch.
STARTER_CATALOGUE = [
    ("Dolo 650", "Paracetamol", "650 mg", "tablet", 15), ("Calpol 650", "Paracetamol", "650 mg", "tablet", 15),
    ("Crocin 500", "Paracetamol", "500 mg", "tablet", 15), ("Calpol 500", "Paracetamol", "500 mg", "tablet", 15),
    ("Brufen 400", "Ibuprofen", "400 mg", "tablet", 15),
    ("Pan 40", "Pantoprazole", "40 mg", "tablet", 15), ("Pantocid 40", "Pantoprazole", "40 mg", "tablet", 15),
    ("Omez 20", "Omeprazole", "20 mg", "capsule", 15),
    ("Azithral 500", "Azithromycin", "500 mg", "tablet", 5), ("Azee 500", "Azithromycin", "500 mg", "tablet", 3),
    ("Cetzine 10", "Cetirizine", "10 mg", "tablet", 10), ("Okacet 10", "Cetirizine", "10 mg", "tablet", 10),
    ("Allegra 120", "Fexofenadine", "120 mg", "tablet", 10),
    ("Glycomet 500", "Metformin", "500 mg", "tablet", 20),
    ("Telma 40", "Telmisartan", "40 mg", "tablet", 15),
    ("Amlong 5", "Amlodipine", "5 mg", "tablet", 15), ("Stamlo 5", "Amlodipine", "5 mg", "tablet", 15),
    ("Atorva 10", "Atorvastatin", "10 mg", "tablet", 15), ("Lipicure 10", "Atorvastatin", "10 mg", "tablet", 10),
    ("Ecosprin 75", "Aspirin", "75 mg", "tablet", 14),
    ("Thyronorm 50", "Levothyroxine", "50 mcg", "tablet", 100), ("Eltroxin 50", "Levothyroxine", "50 mcg", "tablet", 100),
    ("Ondem 4", "Ondansetron", "4 mg", "tablet", 10),
    # Long-term and speciality medicines (refills, forecasts, care-area insights)
    ("Amaryl 1", "Glimepiride", "1 mg", "tablet", 30), ("Januvia 100", "Sitagliptin", "100 mg", "tablet", 7),
    ("Galvus 50", "Vildagliptin", "50 mg", "tablet", 15), ("Rosuvas 10", "Rosuvastatin", "10 mg", "tablet", 15),
    ("Met XL 25", "Metoprolol", "25 mg", "tablet", 15), ("Clopilet 75", "Clopidogrel", "75 mg", "tablet", 15),
    ("Montair 10", "Montelukast", "10 mg", "tablet", 15), ("Levocet 5", "Levocetirizine", "5 mg", "tablet", 10),
    ("Asthalin Inhaler", "Salbutamol", "100 mcg", "inhaler", 1),
    ("Foracort 200 Rotacap", "Budesonide + Formoterol", "200 mcg", "capsule", 30),
    ("Augmentin 625 Duo", "Amoxicillin + Clavulanic acid", "625 mg", "tablet", 10),
    ("Taxim-O 200", "Cefixime", "200 mg", "tablet", 10), ("Doxy-1 100", "Doxycycline", "100 mg", "capsule", 10),
    ("Niftran 100", "Nitrofurantoin", "100 mg", "capsule", 10), ("Urimax 0.4", "Tamsulosin", "0.4 mg", "capsule", 15),
    ("Nexito 10", "Escitalopram", "10 mg", "tablet", 10), ("Serta 50", "Sertraline", "50 mg", "tablet", 10),
    ("Lyrica 75", "Pregabalin", "75 mg", "capsule", 14), ("Hifenac 100", "Aceclofenac", "100 mg", "tablet", 10),
    ("Voveran 50", "Diclofenac", "50 mg", "tablet", 10), ("Domstal 10", "Domperidone", "10 mg", "tablet", 10),
    ("Razo 20", "Rabeprazole", "20 mg", "tablet", 15), ("Udiliv 300", "Ursodeoxycholic acid", "300 mg", "tablet", 15),
    ("Uprise D3 60K", "Cholecalciferol", "60000 IU", "capsule", 4),
    ("Methycobal 500", "Methylcobalamin", "500 mcg", "tablet", 10), ("Folvite 5", "Folic acid", "5 mg", "tablet", 45),
    ("Orofer XT", "Ferrous ascorbate", "100 mg", "tablet", 10), ("Terbicip 250", "Terbinafine", "250 mg", "tablet", 7),
    ("Moxicip Eye Drops", "Moxifloxacin", "0.5%", "drops", 1),
    ("Refresh Tears", "Carboxymethylcellulose", "0.5%", "drops", 1),
    ("Hexidine Mouthwash", "Chlorhexidine", "0.2%", "other", 1),
]


def seed_catalogue() -> None:
    """Add any starter medicines that are missing and fill in care areas from the salt."""
    with get_db() as conn:
        conn.executemany("INSERT OR IGNORE INTO medicines (name, generic_name, strength, form, pack_size, rx_required, "
                         "created_at, care_area) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                         [(n, g, s, f, p, stamp(), ref.care_area_for(g)) for n, g, s, f, p in STARTER_CATALOGUE])
        for row in conn.execute("SELECT id, generic_name FROM medicines WHERE care_area IS NULL").fetchall():
            area = ref.care_area_for(row["generic_name"])
            if area:
                conn.execute("UPDATE medicines SET care_area = ? WHERE id = ?", (area, row["id"]))
        salts.backfill(conn)


# Imported last, so slices can depend on each other in any order
from features.medicines.api import MedicineIn  # noqa: E402
