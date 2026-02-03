"""
Middleware سفارشی برای مدیریت بهتر خطاهای CSRF و انتخاب کافه
"""
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.middleware.csrf import CsrfViewMiddleware
from django.http import Http404
from django.urls import resolve


class CustomCsrfViewMiddleware(CsrfViewMiddleware):
    """
    Middleware سفارشی که به جای نمایش خطای 403، 
    کاربر را به صفحه لاگین هدایت می‌کند
    """
    
    def process_view(self, request, callback, callback_args, callback_kwargs):
        # اجرای middleware پیش‌فرض CSRF
        result = super().process_view(request, callback, callback_args, callback_kwargs)
        
        # اگر خطای CSRF رخ داد (403)
        if result is not None and result.status_code == 403:
            # اگر درخواست از صفحه لاگین بود
            if request.path == '/login/':
                messages.error(request, 'نشست شما منقضی شده است. لطفاً دوباره تلاش کنید.')
                return redirect('login')
        
        return result


class CafeMiddleware:
    """
    Middleware برای مدیریت انتخاب و دسترسی به کافه
    - کاربران عادی فقط به کافه خودشان دسترسی دارند
    - کاربران ادمین (manager, finance_manager, accounting, ceo) باید ابتدا کافه را انتخاب کنند
    - فقط manager و ceo می‌توانند بین کافه‌ها جابجا شوند
    """
    
    # مسیرهایی که نیاز به کافه ندارند
    EXEMPT_PATHS = [
        '/login/',
        '/logout/',
        '/select-cafe/',
        '/admin/',
        '/static/',
        '/media/',
        '/health/',
        '/accounts/login/',
    ]
    
    # Role هایی که باید کافه انتخاب کنند
    CAFE_SELECTION_ROLES = ['manager', 'finance_manager', 'accounting', 'ceo']
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Import here to avoid circular imports
        from .models import Cafe
        
        # بررسی مسیرهای exempt
        path = request.path
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return self.get_response(request)
        
        # اگر کاربر لاگین نیست، اجازه بده ادامه بده (login_required خودش handle می‌کنه)
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # استخراج cafe_code از URL اگر وجود داره
        cafe_code = self._extract_cafe_code(request)
        
        if cafe_code:
            # اعتبارسنجی کافه
            cafe = self._validate_cafe_access(request, cafe_code, Cafe)
            if cafe is None:
                # کافه معتبر نیست یا کاربر دسترسی ندارد
                if request.user.needs_cafe_selection:
                    return redirect('select_cafe')
                else:
                    raise Http404("کافه یافت نشد یا دسترسی ندارید")
            
            # ذخیره کافه در session و request
            request.session['selected_cafe_id'] = cafe.id
            request.session['selected_cafe_code'] = cafe.code
            request.cafe = cafe
        else:
            # مسیر بدون cafe_code - فقط برای کاربران ادمین که هنوز کافه انتخاب نکردن
            if request.user.needs_cafe_selection:
                # اگر کافه در session نیست، به صفحه انتخاب کافه بفرست
                if not request.session.get('selected_cafe_id'):
                    return redirect('select_cafe')
                else:
                    # کافه در session هست، تنظیم کن
                    try:
                        request.cafe = Cafe.objects.get(id=request.session['selected_cafe_id'])
                    except Cafe.DoesNotExist:
                        del request.session['selected_cafe_id']
                        return redirect('select_cafe')
            else:
                # کاربر عادی باید کافه داشته باشه
                if not request.user.cafe:
                    # کاربر عادی بدون کافه - خطا
                    raise Http404("کافه برای کاربر تعریف نشده است. با مدیر تماس بگیرید.")
                
                # تنظیم کافه کاربر
                request.session['selected_cafe_id'] = request.user.cafe_id
                request.session['selected_cafe_code'] = request.user.cafe.code
                request.cafe = request.user.cafe
        
        response = self.get_response(request)
        return response
    
    def _extract_cafe_code(self, request):
        """استخراج cafe_code از URL"""
        try:
            resolved = resolve(request.path)
            return resolved.kwargs.get('cafe_code')
        except:
            return None
    
    def _validate_cafe_access(self, request, cafe_code, Cafe):
        """
        اعتبارسنجی دسترسی کاربر به کافه
        Returns: Cafe object if valid, None otherwise
        
        منطق:
        - کاربران با cafe=None و نقش ادمین (سوپر ادمین): به همه کافه‌ها دسترسی دارند
        - سایر کاربران: فقط به کافه خودشان دسترسی دارند
        """
        try:
            cafe = Cafe.objects.get(code=cafe_code, is_active=True)
        except Cafe.DoesNotExist:
            return None
        
        user = request.user
        
        # سوپر ادمین‌ها (cafe=None و نقش ادمین) به همه کافه‌ها دسترسی دارند
        if user.cafe is None and user.role in self.CAFE_SELECTION_ROLES:
            return cafe
        
        # سایر کاربران فقط به کافه خودشان دسترسی دارند
        if user.cafe_id == cafe.id:
            return cafe
        
        return None


class CafeContextMiddleware:
    """
    Middleware برای اضافه کردن اطلاعات کافه به context
    این باید بعد از CafeMiddleware باشه
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Import here to avoid circular imports
        from .models import Cafe
        
        # اضافه کردن cafe به request اگر وجود نداره
        if not hasattr(request, 'cafe'):
            cafe_id = request.session.get('selected_cafe_id')
            if cafe_id:
                try:
                    request.cafe = Cafe.objects.get(id=cafe_id)
                except Cafe.DoesNotExist:
                    request.cafe = None
            else:
                request.cafe = None
        
        response = self.get_response(request)
        return response


def get_current_cafe(request):
    """Helper function برای گرفتن کافه فعلی از request"""
    from .models import Cafe
    
    if hasattr(request, 'cafe'):
        return request.cafe
    
    cafe_id = request.session.get('selected_cafe_id')
    if cafe_id:
        try:
            return Cafe.objects.get(id=cafe_id)
        except Cafe.DoesNotExist:
            return None
    return None


def get_cafe_code(request):
    """Helper function برای گرفتن کد کافه از request"""
    if hasattr(request, 'cafe') and request.cafe:
        return request.cafe.code
    return request.session.get('selected_cafe_code')
