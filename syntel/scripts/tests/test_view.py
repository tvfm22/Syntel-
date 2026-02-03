from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def price_test(request):
    """صفحه تست برای رفرش خودکار کارت‌ها"""
    return render(request, 'price_test.html')
