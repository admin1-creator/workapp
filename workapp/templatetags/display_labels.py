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
    contractor_name = _value(record, "general_contractor")
    if site and contractor_name:
        return f"{site}（{contractor_name}）"
    return site or contractor_name


@register.filter
def upper_company(record):
    return _value(record, "primary_company") or _value(record, "general_contractor")


@register.filter
def short_date(value):
    if hasattr(value, "month") and hasattr(value, "day"):
        return f"{value.month}/{value.day}"
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        month = text[5:7].lstrip("0") or "0"
        day = text[8:10].lstrip("0") or "0"
        return f"{month}/{day}"
    return text


@register.filter
def comma_num(value):
    if value is None or value == "":
        return ""
    try:
        number = int(value)
    except TypeError, ValueError:
        return value
    return f"{number:,}"
