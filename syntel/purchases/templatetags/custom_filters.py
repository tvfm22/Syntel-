from django import template
from django.utils.numberformat import format

register = template.Library()

@register.filter
def number_format(value):
    """
    Format a number with Persian/Arabic numerals and thousand separators
    """
    if value is None:
        return '0'

    try:
        # Format the number with thousand separators
        formatted = format(value, '.', 0)

        # Convert Western Arabic numerals to Persian/Arabic numerals
        persian_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
        return formatted.translate(persian_digits)
    except (ValueError, TypeError):
        return str(value)