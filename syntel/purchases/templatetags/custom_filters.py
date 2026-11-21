from django import template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
import jdatetime
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from purchases.numbering import request_number as request_number_for

logger = logging.getLogger(__name__)

register = template.Library()

_PERSIAN_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


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
        value = _as_local(value)
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
    if value is None or value == '':
        return '۰'

    try:
        d = Decimal(str(value).replace(',', '').strip())
        if not d.is_finite():
            return str(value)
        rounded = d.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        formatted = f"{rounded:,}"
        return formatted.translate(_PERSIAN_DIGITS)
    except (InvalidOperation, ValueError, TypeError):
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
        if isinstance(value, datetime):
            jalali = jdatetime.datetime.fromgregorian(datetime=value)
        elif isinstance(value, date):
            jalali = jdatetime.date.fromgregorian(date=value)
        else:
            jalali = jdatetime.date.fromgregorian(date=value)
        
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
        
        return result.translate(_PERSIAN_DIGITS)
    except Exception:
        logger.exception('jalali_date filter error')
        return str(value)


@register.filter(name='jalali_datetime')
def jalali_datetime(value, format_string='%Y/%m/%d %H:%M'):
    """
    Convert datetime or date to Jalali (Persian) date and time
    Usage: {{ datetime_field|jalali_datetime }}
    Usage: {{ datetime_field|jalali_datetime:"%d %B %Y ساعت %H:%M" }}
    """
    if not value:
        return ''
    
    try:
        value = _as_local(value)
        if isinstance(value, datetime):
            jalali = jdatetime.datetime.fromgregorian(datetime=value)
        elif isinstance(value, date):
            jalali = jdatetime.datetime.fromgregorian(date=value)
        else:
            return str(value)
        
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
        
        return result.translate(_PERSIAN_DIGITS)
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
            result = format_string
            result = result.replace('%H', str(value.hour).zfill(2))
            result = result.replace('%M', str(value.minute).zfill(2))
            result = result.replace('%S', str(value.second).zfill(2))
            
            return result.translate(_PERSIAN_DIGITS)
    except Exception:
        logger.exception('jalali_time filter error')

    return str(value)


# =====================================================
# Cafe URL Template Tags
# =====================================================


@register.simple_tag(takes_context=True)
def cafe_url(context, url_name, *args, **kwargs):
    """
    Generate URL with cafe_code prefix when an active cafe context exists.
    Usage: {% cafe_url 'dashboard' %}
           {% cafe_url 'product_update' product_id=123 %}
    """
    request = context.get('request')
    cafe_code = context.get('cafe_code') or kwargs.pop('cafe_code', None)
    
    if not cafe_code and request:
        if hasattr(request, 'cafe') and request.cafe:
            cafe_code = request.cafe.code
        elif hasattr(request, 'session') and request.session.get('selected_cafe_code'):
            cafe_code = request.session['selected_cafe_code']
        elif hasattr(request, 'user') and request.user.is_authenticated and getattr(request.user, 'cafe', None):
            cafe_code = request.user.cafe.code
    
    if cafe_code:
        kwargs['cafe_code'] = cafe_code
        return reverse(url_name, args=args, kwargs=kwargs)

    try:
        return reverse(url_name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return '#'

@register.filter
def get_item(dictionary, key):
    """
    Get item from dictionary by key
    Usage: {{ my_dict|get_item:key_name }}
    """
    if not isinstance(dictionary, dict):
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
    if value is None or value == '':
        return '—'
    try:
        d = Decimal(str(value).replace(',', '').strip())
        if not d.is_finite():
            return str(value)
        q = d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    text = f'{q:,}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text.translate(_PERSIAN_DIGITS)
