"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

_django_app = None


def application(environ, start_response):
    if environ.get("PATH_INFO") == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"ok"]
    global _django_app
    if _django_app is None:
        from django.core.wsgi import get_wsgi_application
        _django_app = get_wsgi_application()
    return _django_app(environ, start_response)
