from django.conf import settings


def issuer(request):
    return {
        "issuer_name": getattr(settings, "ISSUER_NAME", "") or "",
        "issuer_address": getattr(settings, "ISSUER_ADDRESS", "") or "",
        "issuer_tel": getattr(settings, "ISSUER_TEL", "") or "",
        "issuer_representative": getattr(settings, "ISSUER_REPRESENTATIVE", "") or "",
    }
