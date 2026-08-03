"""ASGI entrypoint. Not used by the MVP deployment, which is WSGI + Gunicorn."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
