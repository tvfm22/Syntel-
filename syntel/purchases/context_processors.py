"""
Context Processors for Cafe Multi-Tenant Support
"""
import logging

from .middleware import _user_can_access_cafe
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
        'other_cafe': None,
    }

    if not request.user.is_authenticated:
        return context

    cafe = None
    cafe_code = None

    # 1. از request.cafe که middleware تنظیم کرده
    if hasattr(request, 'cafe') and request.cafe and request.cafe.is_active:
        cafe = request.cafe
        cafe_code = request.cafe.code

    # 2. از session (با بررسی دسترسی و فعال بودن کافه)
    elif request.session.get('selected_cafe_id'):
        session_cafe = Cafe.objects.filter(
            id=request.session['selected_cafe_id'],
            is_active=True,
        ).first()
        if session_cafe and _user_can_access_cafe(request.user, session_cafe):
            cafe = session_cafe
            cafe_code = session_cafe.code

    # 3. از کاربر (کاربران عادی)
    if not cafe and request.user.cafe and request.user.cafe.is_active:
        cafe = request.user.cafe
        cafe_code = request.user.cafe.code

    context['current_cafe'] = cafe
    context['cafe_code'] = cafe_code

    can_switch = bool(request.user.is_superuser or request.user.can_switch_cafe)
    context['can_switch_cafe'] = can_switch

    if can_switch:
        cafes = list(Cafe.objects.filter(is_active=True))
        context['all_cafes'] = cafes

        if cafe:
            context['other_cafe'] = next(
                (c for c in cafes if c.id != cafe.id), None
            )
        elif cafes:
            context['other_cafe'] = cafes[0]

    return context
