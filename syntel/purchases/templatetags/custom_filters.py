from django import template
from django.utils import timezone
import jdatetime
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from purchases.numbering import request_number as request_number_for

logger = logging.getLogger(__name__)

register = template.Library()


def _as_local(value):
    """Datetimes are stored in UTC; render them in the active timezone."""
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


@register.filter(name='request_number')
def request_number_filter(value):
    """Render a raw request id as the human-facing request number."""
    return request_number_for(value)

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
    except Exception:
        logger.exception('iso_date filter error')
        return str(value)

@register.filter
def number_format(value):
    """
    Format a number with thousand separators (comma) and Persian numerals
    """
    if value is None:
        return '0'

    try:
        formatted = "{:,.0f}".format(float(value))
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
        value = _as_local(value)
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
    except Exception:
        logger.exception('jalali_date filter error')
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
        value = _as_local(value)
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
    except Exception:
        logger.exception('jalali_datetime filter error')
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
        value = _as_local(value)
        if hasattr(value, 'hour'):
            # datetime or time object
            result = format_string
            result = result.replace('%H', str(value.hour).zfill(2))
            result = result.replace('%M', str(value.minute).zfill(2))
            result = result.replace('%S', str(value.second).zfill(2))
            
            # Convert numbers to Persian
            persian_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
            return result.translate(persian_digits)
    except Exception:
        logger.exception('jalali_time filter error')

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

@register.filter
def get_item(dictionary, key):
    """
    Get item from dictionary by key
    Usage: {{ my_dict|get_item:key_name }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def trim_decimal(value, places=2):
    """
    نمایش عدد تا حداکثر `places` رقم اعشار بدون صفرهای اضافی
    5.0000 → '5'، 0.4000 → '0.4'، 1.5000 → '1.5'، 0.25 → '0.25'
    """
    if value is None or value == '':
        return '0'
    try:
        d = Decimal(str(value)).quantize(Decimal('0.1') ** int(places), rounding=ROUND_HALF_UP)
        s = format(d.normalize(), 'f')
        if '.' in s:
            s = s.rstrip('0').rstrip('.')
        return s
    except (ValueError, TypeError, ArithmeticError):
        return str(value)


@register.filter
def div(value, divisor):
    """تقسیم مقدار بر مقسوم‌علیه - برگرداندن Decimal"""
    if value is None or divisor in (None, '', 0):
        return None
    try:
        return Decimal(str(value)) / Decimal(str(divisor))
    except (ValueError, TypeError, ArithmeticError):
        return None


@register.filter
def num2(value):
    """Persian digits with thousands separators, up to 2 decimals (drops trailing zeros)."""
    if value is None:
        return '—'
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f'{v:,.2f}'.rstrip('0').rstrip('.')
    return text.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
