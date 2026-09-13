from django import template

register = template.Library()


def _value(obj, key):
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return str(value or "").strip()


@register.filter
def site_with_gc(record):
    site = _value(record, "site")
    gc = _value(record, "general_contractor")
    if site and gc:
        return f"{site}（{gc}）"
    return site or gc


@register.filter
def upper_company(record):
    return _value(record, "primary_company") or _value(record, "general_contractor")
