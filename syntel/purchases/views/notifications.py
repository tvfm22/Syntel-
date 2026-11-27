from __future__ import annotations

from ..models import Notification
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_object_or_404
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST


@custom_login_required
def notifications_list(request, cafe_code=None):
    notifications = filter_by_cafe(
        Notification.objects.filter(recipient=request.user), request
    ).order_by("-created_at")
    return render(request, "notifications_list.html", {"notifications": notifications})


@custom_login_required
def notification_detail(request, notification_id):
    notification = get_scoped_object_or_404(
        Notification, request, id=notification_id, recipient=request.user
    )
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    return render(request, "notification_detail.html", {"notification": notification})


def _wants_json_response(request):
    accept = request.headers.get('Accept', '')
    ctype = request.content_type or ''
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in accept
        or 'application/json' in ctype
    )


@custom_login_required
@require_POST
def mark_notification_read(request, notification_id):
    notification = get_scoped_object_or_404(
        Notification, request, id=notification_id, recipient=request.user
    )
    notification.is_read = True
    notification.save(update_fields=['is_read'])
    if _wants_json_response(request):
        return JsonResponse({'success': True})
    return get_cafe_redirect_url("notifications_list", request)


@custom_login_required
@require_POST
def delete_notification(request, notification_id):
    notification = get_scoped_object_or_404(
        Notification, request, id=notification_id, recipient=request.user
    )
    notification.delete()
    if _wants_json_response(request):
        return JsonResponse({'success': True})
    return get_cafe_redirect_url("notifications_list", request)


@custom_login_required
def get_unread_notifications_count(request):
    count = filter_by_cafe(
        Notification.objects.filter(recipient=request.user, is_read=False), request
    ).count()
    return JsonResponse({"count": count})


@custom_login_required
def get_recent_notifications(request):
    notifications = filter_by_cafe(
        Notification.objects.filter(recipient=request.user), request
    ).order_by("-created_at")[:5]
    data = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        }
        for n in notifications
    ]
    return JsonResponse({"notifications": data})
