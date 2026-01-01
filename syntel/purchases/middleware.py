"""
Middleware سفارشی برای مدیریت بهتر خطاهای CSRF
"""
from django.shortcuts import redirect
from django.contrib import messages
from django.middleware.csrf import CsrfViewMiddleware


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
