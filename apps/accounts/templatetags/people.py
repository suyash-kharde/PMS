from django import template

register = template.Library()


@register.filter
def hours_minutes(seconds):
    """3900 -> '1 hr 5 min', 3600 -> '1 hr', 240 -> '4 min', 0 -> '0 min'."""
    try:
        minutes = int(seconds) // 60
    except (TypeError, ValueError):
        return "0 min"
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} hr {minutes} min" if minutes else f"{hours} hr"
