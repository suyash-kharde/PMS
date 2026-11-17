from django import template

from ..permissions import has_cap as _has_cap

register = template.Library()


@register.simple_tag
def can(user, code, obj=None):
    return _has_cap(user, code, obj)


@register.filter(name="can")
def can_filter(user, code):
    return _has_cap(user, code)


@register.filter(name="can_any")
def can_any_filter(user, codes):
    """True when the user holds at least one of the comma-separated capability codes."""
    return any(_has_cap(user, code.strip()) for code in codes.split(",") if code.strip())
