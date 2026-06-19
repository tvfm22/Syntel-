"""
Context Processors for Cafe Multi-Tenant Support
"""
import logging

from .models import Cafe

logger = logging.getLogger(__name__)


def cafe_context(request):
    """
    اضافه کردن اطلاعات کافه به تمام templates
    """
    context = {
        'current_cafe': None,
        'cafe_code': None,
        'can_switch_cafe': False,
        'all_cafes': [],
    }
    
    if not request.user.is_authenticated:
        return context
    
    # گرفتن کافه فعلی - روش‌های مختلف را امتحان کن
    cafe = None
    cafe_code = None
    
    # 1. از request.cafe که middleware تنظیم کرده
    if hasattr(request, 'cafe') and request.cafe:
        cafe = request.cafe
        cafe_code = request.cafe.code
    
    # 2. از session
    elif request.session.get('selected_cafe_id'):
        try:
            cafe = Cafe.objects.get(id=request.session['selected_cafe_id'])
            cafe_code = cafe.code
        except Cafe.DoesNotExist:
            pass
    
    # 3. از کاربر (کاربران عادی)
    if not cafe and request.user.cafe:
        cafe = request.user.cafe
        cafe_code = request.user.cafe.code
    
    # 4. Fallback به local (اگر هیچ کدام نبود)
    if not cafe_code:
        try:
            default_cafe = Cafe.objects.filter(code='local', is_active=True).first()
            if default_cafe:
                cafe = default_cafe
                cafe_code = 'local'
        except Exception:
            logger.exception('Error fetching default cafe in context processor')
    
    context['current_cafe'] = cafe
    context['cafe_code'] = cafe_code
    
    # بررسی امکان تغییر کافه (فقط سوپر ادمین‌ها)
    context['can_switch_cafe'] = request.user.can_switch_cafe
    
    # لیست کافه‌ها برای کاربرانی که می‌توانند تغییر کافه دهند
    if context['can_switch_cafe']:
        context['all_cafes'] = Cafe.objects.filter(is_active=True)
    
    return context