"""
URL configuration for syntel project.
"""
import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.core.exceptions import SuspiciousFileOperation
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.urls import include, path, re_path
from django.utils._os import safe_join
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.permissions import IsAdminUser

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "HEAD"])
def health_check(request):
    """Simple health check endpoint - always returns 200 OK."""
    return HttpResponse("OK", status=200)


@csrf_exempt
@require_http_methods(["GET"])
def health_check_detailed(request):
    """Detailed health check endpoint for monitoring (never leaks raw DB errors)."""
    health_status = {
        'status': 'healthy',
        'database': 'ok',
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("Detailed health check database probe failed")
        health_status['database'] = 'error'
        health_status['status'] = 'unhealthy'

    if health_status['status'] == 'unhealthy':
        return JsonResponse(health_status, status=503)
    return JsonResponse(health_status, status=200)


@login_required(login_url='/login/')
@require_http_methods(["GET", "HEAD"])
def protected_media_serve(request, path):
    """Serve uploaded media files only to authenticated active users with tenant isolation."""
    if not request.user.is_active:
        return HttpResponse(status=403)

    norm_path = path.lstrip('/').replace('\\', '/')
    if (
        norm_path.startswith('invoices/')
        or norm_path.startswith('debts/payments/')
        or norm_path.startswith('assets/')
    ):
        from django.db.models import Q
        from purchases.models import (
            AssetAuditItem,
            AssetCatalog,
            AssetMovement,
            AssetUnit,
            Debt,
            DebtPayment,
            PurchaseInvoice,
            User,
        )

        is_global = request.user.is_superuser or (
            request.user.cafe_id is None
            and getattr(request.user, 'role', None) in User.CAFE_SELECTION_ROLES
        )
        if not is_global:
            user_cafe_id = getattr(request.user, 'cafe_id', None)
            if user_cafe_id is None:
                return HttpResponse(status=403)
            if norm_path.startswith('invoices/'):
                inv = (
                    PurchaseInvoice.objects.filter(invoice_file=norm_path)
                    .select_related('request')
                    .first()
                )
                if inv is None:
                    raise Http404("Media file not found")
                if inv.request.cafe_id != user_cafe_id:
                    return HttpResponse(status=403)
            elif norm_path.startswith('assets/'):
                cafe_ids = (
                    list(
                        AssetCatalog.objects.filter(
                            Q(image=norm_path) | Q(thumbnail=norm_path)
                        ).values_list('cafe_id', flat=True)
                    )
                    + list(
                        AssetUnit.objects.filter(photo=norm_path).values_list('cafe_id', flat=True)
                    )
                    + list(
                        AssetMovement.objects.filter(evidence_photo=norm_path).values_list('cafe_id', flat=True)
                    )
                    + list(
                        AssetAuditItem.objects.filter(evidence_photo=norm_path).values_list('audit__cafe_id', flat=True)
                    )
                )
                if not cafe_ids:
                    raise Http404("Media file not found")
                if user_cafe_id not in cafe_ids:
                    return HttpResponse(status=403)
            else:
                cafe_ids = list(
                    Debt.objects.filter(payment_photo=norm_path).values_list('cafe_id', flat=True)
                ) + list(
                    DebtPayment.objects.filter(payment_photo=norm_path).values_list('cafe_id', flat=True)
                )
                if not cafe_ids:
                    raise Http404("Media file not found")
                if user_cafe_id not in cafe_ids:
                    return HttpResponse(status=403)

    try:
        full_path = Path(safe_join(str(settings.MEDIA_ROOT), path))
    except SuspiciousFileOperation:
        raise Http404("Invalid media path")
    if not full_path.is_file():
        raise Http404("Media file not found")
    content_type, _ = mimetypes.guess_type(str(full_path))
    return FileResponse(
        open(full_path, 'rb'),
        content_type=content_type or 'application/octet-stream',
    )


urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health_check'),
    path('health/detailed/', health_check_detailed, name='health_check_detailed'),
    path('', include('purchases.urls')),
    # API Documentation — restricted to admin users
    path(
        'api/schema/',
        SpectacularAPIView.as_view(permission_classes=[IsAdminUser]),
        name='schema',
    ),
    path(
        'api/schema/swagger-ui/',
        SpectacularSwaggerView.as_view(url_name='schema', permission_classes=[IsAdminUser]),
        name='swagger-ui',
    ),
    path(
        'api/schema/redoc/',
        SpectacularRedocView.as_view(url_name='schema', permission_classes=[IsAdminUser]),
        name='redoc',
    ),
    path(
        'swagger/',
        SpectacularSwaggerView.as_view(url_name='schema', permission_classes=[IsAdminUser]),
        name='legacy-swagger-ui',
    ),
    path(
        'redoc/',
        SpectacularRedocView.as_view(url_name='schema', permission_classes=[IsAdminUser]),
        name='legacy-redoc',
    ),
]

if settings.MEDIA_URL and settings.MEDIA_ROOT:
    media_prefix = settings.MEDIA_URL.lstrip('/')
    urlpatterns += [
        re_path(rf'^{media_prefix}(?P<path>.*)$', protected_media_serve, name='protected_media'),
    ]
