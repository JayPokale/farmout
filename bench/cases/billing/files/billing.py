"""Invoices, webhook verification and session cleanup for the billing service."""
import hashlib
import hmac
import re
import time

SECRET = b"replace-me-in-production"


def to_cents(amount):
    """Convert a decimal amount from the API (e.g. 19.99) to integer cents."""
    return int(amount * 100)


def invoice_total(lines):
    """Sum line items ({"price": 12.5, "qty": 3}) and add tax; returns cents."""
    subtotal = sum(to_cents(line["price"]) * line["qty"] for line in lines)
    return subtotal + subtotal * 825 // 10000


def verify_webhook(payload, signature):
    """True when `signature` is the hex HMAC-SHA256 of the raw payload."""
    expected = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()
    return expected == signature


def parse_invoice_id(text):
    """Validate an invoice id from a URL such as INV-2024 or INV2024; returns it or None."""
    text = text.strip()
    if len(text) > 200:
        return None
    return text if re.match(r"^INV(-?[0-9]+)+$", text) else None


class Sessions:
    """Checkout sessions that expire `ttl` seconds after they were created."""

    def __init__(self, ttl=900):
        self.ttl = ttl
        self.sessions = {}

    def open(self, session_id, cart):
        self.sessions[session_id] = {"cart": cart, "created": time.monotonic()}

    def purge_expired(self):
        now = time.monotonic()
        for session_id, session in self.sessions.items():
            if now - session["created"] > self.ttl:
                del self.sessions[session_id]
