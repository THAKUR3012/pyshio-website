"""Pharmacy offers: prescriptions go to nearby pharmacies, they quote, the patient compares prices.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, money, render


def offer_lines(tops: dict) -> list:
    """Email rows for the cheapest / best discount / fastest delivery offers."""
    rows = []
    if not tops:
        return rows
    c, d, f = tops.get("cheapest"), tops.get("best_discount"), tops.get("fastest_delivery")
    if c:
        rows.append(("Cheapest", f"{money(c['total_paise'])} at {c['store_name']} ({c['distance_km']:g} km)"))
    if d:
        rows.append(("Best discount", f"{d['discount_percent']:g}% off MRP at {d['store_name']} ({money(d['total_paise'])})"))
    if f:
        rows.append(("Fastest delivery", f"about {f['delivery_minutes']} min from {f['store_name']} "
                                         f"({money(f['total_paise'])} + {money(f['delivery_fee_paise'] or 0)} delivery)"))
    return rows


def send_rx_request_to_pharmacy(pharmacy: dict, rx: dict, distance_km: float) -> bool:
    text, html = render(
        "New prescription near you",
        f"A patient {distance_km:g} km from {pharmacy['store_name']} needs these medicines. Send your best price.",
        [("Prescription", rx["reference"]), ("Items", str(len(rx["items"]))),
         ("Medicines", ", ".join(f"{i['name']} × {i['quantity']}" for i in rx["items"]))],
        "Patients compare the cheapest price, best discount and fastest delivery. Quote fast to win the order.",
        "#4f46e5", button=("Send a quote", link("/pharmacy")))
    return mailer.send_email(pharmacy["email"], f"New prescription {distance_km:g} km away ({rx['reference']})", text, html)


WIN_LABELS = {"cheapest": "Cheapest price", "best_discount": "Best discount", "fastest_delivery": "Fastest delivery"}


def send_cheaper_quote(patient: dict, rx_reference: str, offer: dict, wins: list = ("cheapest",)) -> bool:
    """A pharmacy's new quote now leads on price, discount and/or delivery speed."""
    titles = " & ".join(WIN_LABELS[w] for w in wins)
    details = [("Prescription", rx_reference), ("Total", money(offer["total_paise"])),
               ("Items available", f"{offer['available']} of {offer['items']}")]
    if offer.get("discount_percent"):
        details.append(("Discount", f"{offer['discount_percent']:g}% off MRP (you save {money(offer['discount_paise'])})"))
    details.append(("Delivery", f"about {offer['delivery_minutes']} min" if offer.get("delivery_available") else "Pickup only"))
    if offer.get("pickup_minutes") is not None:
        details.append(("Ready for pickup in", f"{offer['pickup_minutes']} min"))
    text, html = render(
        f"New {titles.lower()} for your medicines",
        f"{offer['store_name']} ({offer['distance_km']:g} km away) just sent an offer for your prescription.",
        details, "Open your prescription to compare and order.", "#059669",
        button=("Compare & order", link("/account#prescriptions")))
    return mailer.send_email(patient["email"], f"{titles}: {money(offer['total_paise'])} at {offer['store_name']}", text, html)
