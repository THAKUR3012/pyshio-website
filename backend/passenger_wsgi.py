"""Entry point for cPanel "Setup Python App" (Phusion Passenger), e.g. GoDaddy shared hosting.

Passenger speaks WSGI, while FastAPI is an ASGI app, so a2wsgi bridges the two.
In cPanel set "Application startup file" to passenger_wsgi.py and
"Application Entry point" to application.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from a2wsgi import ASGIMiddleware  # noqa: E402
from server import app  # noqa: E402

application = ASGIMiddleware(app)
