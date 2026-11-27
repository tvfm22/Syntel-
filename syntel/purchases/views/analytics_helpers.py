from __future__ import annotations

from ..models import InvoiceItem
from .helpers import resolve_api_cafe
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from typing import Tuple
import hashlib


ANALYTICS_ALLOWED_ROLES = frozenset({
	'manager', 'ceo', 'finance_manager', 'accounting', 'purchasing', 'admin',
})


VALID_PERIODS = frozenset({'weekly', 'monthly', 'quarterly', 'yearly'})


VALID_GRANULARITIES = frozenset({'daily', 'weekly', 'monthly'})


class HasAnalyticsRole(BasePermission):
	"""فقط نقش‌های مدیریتی، مالی و خرید مجاز به مشاهدهٔ APIهای تحلیلی هستند."""
	def has_permission(self, request, view):
		user = getattr(request, 'user', None)
		if not user or not user.is_authenticated or not user.is_active:
			return False
		if user.is_superuser:
			return True
		return (
			getattr(user, 'role', None) in ANALYTICS_ALLOWED_ROLES
			or getattr(user, 'secondary_role', None) in ANALYTICS_ALLOWED_ROLES
		)


def _require_cafe_or_error(request):
	cafe, err_status, err_msg = resolve_api_cafe(request)
	if err_status:
		return None, Response({'error': err_msg}, status=err_status)
	return cafe, None


def _parse_int_param(raw, name, required=False):
	if raw is None or str(raw).strip() == '':
		if required:
			return None, Response({'error': f'{name} is required'}, status=400)
		return None, None
	s = str(raw).strip()
	if not s.isdigit():
		return None, Response({'error': f'Invalid {name}'}, status=400)
	return int(s), None


def _normalize_period(raw, default='weekly'):
	p = (raw or default).strip()
	return p if p in VALID_PERIODS else default


def _normalize_granularity(raw, default='daily'):
	g = (raw or default).strip()
	return g if g in VALID_GRANULARITIES else default


INVOICE_STATUS_INCLUDED = [
	# Include invoices that represent real financial activity
	'sent', 'approved', 'partially_paid', 'paid', 'overdue'
]


def _invoice_items_for_period(
    *,
    start: datetime,
    end: datetime,
    end_inclusive: bool = True,
    cafe=None,
) -> QuerySet:
	created_at_filter = {
		'invoice__created_at__gte': start,
		('invoice__created_at__lte' if end_inclusive else 'invoice__created_at__lt'): end,
		'invoice__status__in': INVOICE_STATUS_INCLUDED,
	}
	if cafe is not None:
		created_at_filter['invoice__cafe'] = cafe
	return InvoiceItem.objects.filter(**created_at_filter)


def get_date_range(period: str = 'weekly') -> Tuple[datetime, datetime, datetime, datetime]:
	"""Return (current_start, current_end, previous_start, previous_end).

	- weekly: 7 days
	- monthly: 30 days
	- quarterly: 90 days
	- yearly: 365 days
	"""
	now = timezone.localtime(timezone.now())
	current_end = now

	if period == 'weekly':
		delta = timedelta(days=7)
	elif period == 'monthly':
		delta = timedelta(days=30)
	elif period == 'quarterly':
		delta = timedelta(days=90)
	elif period == 'yearly':
		delta = timedelta(days=365)
	else:
		delta = timedelta(days=7)

	current_start = current_end - delta
	previous_end = current_start - timedelta(microseconds=1)
	previous_start = current_start - delta

	return current_start, current_end, previous_start, previous_end


def calculate_change_percent(current_value, previous_value):
	"""Calculate percentage change between two values."""
	current_value = current_value or Decimal('0.00')
	previous_value = previous_value or Decimal('0.00')

	if previous_value == 0:
		if current_value > 0:
			return Decimal('100.00')
		return Decimal('0.00')

	change = ((current_value - previous_value) / previous_value) * 100
	return Decimal(str(round(change, 2)))


def calculate_growth_rate(current, previous):
	"""محاسبه نرخ رشد به درصد"""
	if previous == 0:
		return 100.0 if current > 0 else 0.0
	return ((current - previous) / previous) * 100


def get_color_by_growth(growth_rate):
	"""تولید رنگ بر اساس نرخ رشد"""
	if growth_rate > 0:
		# قرمز برای رشد
		intensity = min(growth_rate / 100, 1.0)
		green = int(99 - intensity * 99)
		blue = int(132 - intensity * 132)
		return f'rgba(255, {green}, {blue}, 0.8)'
	elif growth_rate < 0:
		# سبز برای کاهش
		intensity = min(abs(growth_rate) / 100, 1.0)
		red = int(75 - intensity * 75)
		blue = int(192 - intensity * 92)
		return f'rgba({red}, 192, {blue}, 0.8)'
	else:
		# خاکستری برای بدون تغییر
		return 'rgba(201, 203, 207, 0.8)'


def get_cache_key(prefix, **kwargs):
	"""تولید cache key یکتا و امن با هش SHA-256."""
	serialized = "&".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
	digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:32]
	return f"analytics:{prefix}:{digest}"


def _dept_items_filter(department):
	dept_names = [n for n in (department.name, department.get_name_display()) if n]
	return (
		Q(requester=department)
		| Q(
			requester__isnull=True,
			invoice__purchase_order__request__user__role=department.name,
		)
		| Q(
			requester__isnull=True,
			invoice__purchase_order__request__user__department__name__in=dept_names,
		)
	)
