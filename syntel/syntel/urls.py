"""
URL configuration for syntel project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse, HttpResponse
from django.db import connection
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

@csrf_exempt
@require_http_methods(["GET", "HEAD"])
def health_check(request):
    """Simple health check endpoint - always returns 200 OK."""
    return HttpResponse("OK", status=200)

@csrf_exempt  
@require_http_methods(["GET"])
def health_check_detailed(request):
    """Detailed health check endpoint for monitoring."""
    health_status = {
        'status': 'healthy',
        'database': 'ok',
    }

    # Check database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as e:
        health_status['database'] = f'error: {str(e)}'
        health_status['status'] = 'unhealthy'

    # Set overall status
    if health_status['status'] == 'unhealthy':
        return JsonResponse(health_status, status=503)
    else:
        return JsonResponse(health_status, status=200)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health_check'),
    path('health/detailed/', health_check_detailed, name='health_check_detailed'),
    path('', include('purchases.urls')),
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # Legacy Swagger URLs (redirect)
    path('swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name='legacy-swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='legacy-redoc'),
]

# Serving uploaded media (receipt photos for debt payments, etc.) from MEDIA_ROOT.
# Works in dev and production (gunicorn behind Liara) alike regardless of DEBUG setting.
from django.conf import settings
from django.urls import re_path
from django.views.static import serve

if settings.MEDIA_URL and settings.MEDIA_ROOT:
    media_prefix = settings.MEDIA_URL.lstrip('/')
    urlpatterns += [
        re_path(rf'^{media_prefix}(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
