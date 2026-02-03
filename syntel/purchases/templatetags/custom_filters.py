from django import template
from django.utils.numberformat import format
import jdatetime

register = template.Library()

@register.filter
def abs_value(value):
    """
    Return the absolute value of a number
    Usage: {{ number|abs_value }}
    """
    try:
        return abs(value)
    except (ValueError, TypeError):
        return value

@register.filter
def iso_date(value):
    """
    Return date in ISO format YYYY-MM-DD
    Usage: {{ date|iso_date }}
    """
    if not value:
        return ''
    try:
        if hasattr(value, 'strftime'):
            return value.strftime('%Y-%m-%d')
        return str(value)
    except:
        return str(value)

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


@register.filter(name='jalali_date')
def jalali_date(value, format_string='%Y/%m/%d'):
    """
    Convert datetime to Jalali (Persian) date
    Usage: {{ date_field|jalali_date }}
    Usage: {{ date_field|jalali_date:"%d %B %Y" }}
    """
    if not value:
        return ''
    
    try:
        # Convert to jdatetime
        if hasattr(value, 'date'):
            # datetime object
            jalali = jdatetime.datetime.fromgregorian(datetime=value)
        else:
            # date object
            jalali = jdatetime.date.fromgregorian(date=value)
        
        # Format with Persian month names
        months = {
            1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد',
            4: 'تیر', 5: 'مرداد', 6: 'شهریور',
            7: 'مهر', 8: 'آبان', 9: 'آذر',
            10: 'دی', 11: 'بهمن', 12: 'اسفند'
        }
        
        result = format_string
        result = result.replace('%Y', str(jalali.year))
        result = result.replace('%m', str(jalali.month).zfill(2))
        result = result.replace('%d', str(jalali.day).zfill(2))
        result = result.replace('%B', months.get(jalali.month, ''))
        
        # Convert numbers to Persian
        persian_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
        return result.translate(persian_digits)
    except:
        return str(value)


@register.filter(name='jalali_datetime')
def jalali_datetime(value, format_string='%Y/%m/%d %H:%M'):
    """
    Convert datetime to Jalali (Persian) date and time
    Usage: {{ datetime_field|jalali_datetime }}
    Usage: {{ datetime_field|jalali_datetime:"%d %B %Y ساعت %H:%M" }}
    """
    if not value:
        return ''
    
    try:
        # Convert to jdatetime
        jalali = jdatetime.datetime.fromgregorian(datetime=value)
        
        # Format with Persian month names
        months = {
            1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد',
            4: 'تیر', 5: 'مرداد', 6: 'شهریور',
            7: 'مهر', 8: 'آبان', 9: 'آذر',
            10: 'دی', 11: 'بهمن', 12: 'اسفند'
        }
        
        result = format_string
        result = result.replace('%Y', str(jalali.year))
        result = result.replace('%m', str(jalali.month).zfill(2))
        result = result.replace('%d', str(jalali.day).zfill(2))
        result = result.replace('%B', months.get(jalali.month, ''))
        result = result.replace('%H', str(jalali.hour).zfill(2))
        result = result.replace('%M', str(jalali.minute).zfill(2))
        result = result.replace('%S', str(jalali.second).zfill(2))
        
        # Convert numbers to Persian
        persian_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
        return result.translate(persian_digits)
    except:
        return str(value)


@register.filter(name='jalali_time')
def jalali_time(value, format_string='%H:%M'):
    """
    Format time in Persian numerals
    Usage: {{ time_field|jalali_time }}
    Usage: {{ time_field|jalali_time:"%H:%M:%S" }}
    """
    if not value:
        return ''
    
    try:
        if hasattr(value, 'hour'):
            # datetime or time object
            result = format_string
            result = result.replace('%H', str(value.hour).zfill(2))
            result = result.replace('%M', str(value.minute).zfill(2))
            result = result.replace('%S', str(value.second).zfill(2))
            
            # Convert numbers to Persian
            persian_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
            return result.translate(persian_digits)
    except:
        pass
    
    return str(value)


# =====================================================
# Cafe URL Template Tags
# =====================================================

from django.urls import reverse

@register.simple_tag(takes_context=True)
def cafe_url(context, url_name, *args, **kwargs):
    """
    Generate URL with cafe_code prefix.
    Usage: {% cafe_url 'dashboard' %}
           {% cafe_url 'product_update' product_id=123 %}
    """
    request = context.get('request')
    cafe_code = context.get('cafe_code')
    
    # Try to get cafe_code from different sources
    if not cafe_code and request:
        if hasattr(request, 'cafe') and request.cafe:
            cafe_code = request.cafe.code
        elif hasattr(request, 'session') and request.session.get('selected_cafe_code'):
            cafe_code = request.session['selected_cafe_code']
        elif hasattr(request, 'user') and request.user.is_authenticated and request.user.cafe:
            cafe_code = request.user.cafe.code
    
    if not cafe_code:
        # Fallback to 'local' if no cafe found
        cafe_code = 'local'
    
    kwargs['cafe_code'] = cafe_code
    return reverse(url_name, args=args, kwargs=kwargs)
