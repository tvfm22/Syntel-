from __future__ import annotations

from ..middleware import get_cafe_code
from ..models import Category
from ..models import DEPARTMENT_CHOICES
from ..models import Department
from ..models import InvoiceItem
from ..models import KukerRecipeLine
from ..models import Product
from ..models import StockItem
from ..models import Unit
from ..services.menu_cogs import record_cost_snapshots_for_cafe
from ..services.recipe_gate import PRODUCT_MANAGER_ROLES
from ..services.recipe_gate import assign_rd_category
from ..services.recipe_gate import bypasses_recipe_gate
from ..services.recipe_gate import filter_requestable_products
from ..services.recipe_gate import inactive_product_error
from ..services.recipe_gate import is_rd_product
from ..services.recipe_gate import recipe_gate_error
from ..services.recipe_gate import recipe_required_category_ids
from .helpers import _fmt2
from .helpers import _parse_decimal
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from decimal import Decimal
from django.contrib import messages
from django.db import IntegrityError
from django.db import models
from django.db.models import ProtectedError
from django.http import Http404
from django.http import HttpRequest
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from urllib.parse import urlencode
import json


PRICE_UNIT_CHOICES = ['کیلوگرم', 'لیتر', 'گرم', 'میل', 'عدد', 'پرس', 'بسته', 'اسلایس']


@custom_login_required
@role_required(["purchasing", "manager"])
def product_list(request):
    cafe = get_cafe_from_request(request)
    products = Product.objects.all()
    if cafe:
        products = products.filter(cafe=cafe)

    # قیمت واحد کوچک‌ترین واحد + اینکه کالا در رسپی منو مصرف می‌شود یا نه
    products = products.select_related('category', 'unit', 'cogs_item').prefetch_related('departments')

    # جستجو بر اساس نام محصول
    search = request.GET.get('search', '')
    if search:
        products = products.filter(name__icontains=search)

    # فیلتر بر اساس دسته‌بندی
    category_id = request.GET.get('category', '')
    if category_id:
        products = products.filter(category_id=category_id)

    departments_qs = Department.objects.filter(is_active=True)
    if cafe:
        departments_qs = departments_qs.filter(cafe=cafe)

    # فیلتر بر اساس بخش درخواست
    selected_department = request.GET.get('department', '').strip()
    if selected_department:
        has_any_dept = models.Exists(
            Product.departments.through.objects.filter(product_id=models.OuterRef('pk'))
        )
        if selected_department == 'general':
            products = products.filter(
                ~has_any_dept & (models.Q(usage_location='general') | models.Q(usage_location=''))
            )
        elif selected_department in ('general_kitchen_bar', 'general_other'):
            products = products.filter(
                ~has_any_dept & models.Q(usage_location=selected_department)
            )
        elif selected_department == 'none':
            reverse_old = {v: k for k, v in Product.OLD_USAGE_KEY_MAP.items()}
            legacy_dept_vals = set()
            for dep_name in departments_qs.values_list('name', flat=True):
                legacy_dept_vals.add(dep_name)
                if dep_name in reverse_old:
                    legacy_dept_vals.add(reverse_old[dep_name])
            products = products.filter(
                ~has_any_dept
                & ~models.Q(usage_location__in=['general', 'general_kitchen_bar', 'general_other', ''])
                & ~models.Q(usage_location__in=legacy_dept_vals)
            )
        elif selected_department.isdigit():
            dep_obj = departments_qs.filter(id=int(selected_department)).first()
            if dep_obj:
                matches_dept = models.Exists(
                    Product.departments.through.objects.filter(
                        product_id=models.OuterRef('pk'),
                        department_id=dep_obj.id,
                    )
                )
                reverse_old_key = {v: k for k, v in Product.OLD_USAGE_KEY_MAP.items()}.get(dep_obj.name)
                legacy_q = models.Q(usage_location=dep_obj.name)
                if reverse_old_key:
                    legacy_q = legacy_q | models.Q(usage_location=reverse_old_key)
                products = products.filter(matches_dept | (~has_any_dept & legacy_q))
            else:
                products = products.none()

    # فیلتر بر اساس وضعیت
    is_active = request.GET.get('is_active', '')
    if is_active == 'true':
        products = products.filter(is_active=True)
    elif is_active == 'false':
        products = products.filter(is_active=False)

    # فیلتر بر اساس قیمت‌گذاری (همه / فقط قیمت‌دار / فقط بی‌قیمت)
    pricing = request.GET.get('pricing', '')
    if pricing == 'priced':
        products = products.filter(base_unit_price__isnull=False)
    elif pricing == 'unpriced':
        products = products.filter(base_unit_price__isnull=True)

    products = list(products)
    # مصرف‌شدن در رسپی: یک کوئری برای همه، نه یکی برای هر محصول.
    # (getattr لازم است — دسترسی مستقیم به OneToOne معکوس خطا می‌دهد.)
    linked = {p.pk: getattr(p, 'cogs_item', None) for p in products}
    item_ids = [item.pk for item in linked.values() if item is not None]
    if item_ids:
        recipe_lines = KukerRecipeLine.objects.filter(material_id__in=item_ids)
        if cafe is not None:
            # همان قاعدهٔ درخواست‌کننده: فقط رسپیِ همین کافه معتبر است
            recipe_lines = recipe_lines.filter(item__cafe=cafe)
        used_material_ids = set(
            recipe_lines.values_list('material_id', flat=True))
    else:
        used_material_ids = set()

    # دسته‌های غذایی — برای نشان‌دادن «فعال/مخفی در سفارش» در لیست کالاها
    food_category_ids = recipe_required_category_ids(cafe)
    for product in products:
        item = linked[product.pk]
        product.cogs_item_pk = item.pk if item else None
        product.cogs_used_in_recipe = bool(item and item.pk in used_material_ids)
        product.is_food_category = product.category_id in food_category_ids
        product.is_rd_category = is_rd_product(product)

    # فیلتر «وضعیت رسپی»: راهِ رسیدن به کالاهایی که برای درخواست‌کننده مخفی
    # شده‌اند، وگرنه پیدا کردنشان فقط با اسکرولِ کل لیست ممکن بود.
    recipe_filter = request.GET.get('recipe', '')
    if recipe_filter == 'used':
        products = [p for p in products if p.cogs_used_in_recipe]
    elif recipe_filter == 'missing':
        products = [p for p in products
                    if p.is_food_category and not p.cogs_used_in_recipe]
    elif recipe_filter == 'free':
        products = [p for p in products if not p.is_food_category]

    product_stats = {
        "total": len(products),
        "active": sum(1 for p in products if p.is_active),
        "priced": sum(1 for p in products if p.base_unit_price is not None),
        "in_recipe": sum(1 for p in products if p.cogs_used_in_recipe),
        "missing_recipe": sum(
            1 for p in products if p.is_food_category and not p.cogs_used_in_recipe
        ),
    }

    categories = Category.objects.all()
    if cafe:
        categories = categories.filter(cafe=cafe)
    return render(request, "product_list.html", {
        "products": products,
        "product_stats": product_stats,
        "categories": categories,
        "departments_qs": departments_qs.order_by('name'),
        "search": search,
        "selected_category": category_id,
        "selected_department": selected_department,
        "selected_status": is_active,
        "selected_pricing": pricing,
        "selected_recipe": recipe_filter,
        "unit_choices": PRICE_UNIT_CHOICES,
        "recipe_categories": [c.name for c in categories if c.requires_recipe],
    })


def _parse_conversion_rate(value):
    """تبدیل مقدار ورودی «میزان بسته‌بندی» یا قیمت به عدد اعشاری - خالی به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    return _parse_decimal(value)


def _apply_product_price(product, request, price_field='base_unit_price', unit_field='price_unit'):
    """قیمت وارد‌شده در فرم را نرمال و روی محصول + ماده اولیه منو اعمال می‌کند.

    قیمت خالی یعنی «دست نزن» — مقدار قبلی حفظ می‌شود.
    Returns ``(linked KukerItem or None, price_changed)``.
    """
    from ..services.product_sync import sync_product

    raw_price = request.POST.get(price_field)
    if raw_price is None or str(raw_price).strip() == '':
        return None, False
    price = _parse_conversion_rate(raw_price)
    if price is None or price < 0:
        return None, False
    old_price = product.base_unit_price
    posted_unit = (request.POST.get(unit_field) or '').strip()[:20]
    if not posted_unit and old_price is not None and price == old_price:
        # Price was not changed and no explicit price_unit was sent;
        # avoid re-dividing an already-normalized base_unit_price by 1000.
        item = sync_product(product)
        return item, False
    unit = (posted_unit or product.smallest_unit or '').strip()[:20]
    # sync_product خودش نرمال‌سازی و هم‌گام‌سازی با ماده اولیه منو را انجام می‌دهد
    item = sync_product(product, price=price, unit=unit)
    return item, (product.base_unit_price != old_price)


def _snapshot_costs_after_price_change(request, cafe, item_name):
    """بهای غذاهایی که این ماده اولیه را مصرف می‌کنند دوباره ثبت می‌شود."""
    from ..services.menu_cogs import record_cost_snapshots_for_cafe

    cafe = cafe or get_cafe_from_request(request)
    if cafe is None:
        return
    record_cost_snapshots_for_cafe(cafe, note=f'تغییر قیمت «{item_name}»')


def _parse_int_or_none(value):
    """تبدیل مقدار ورودی عدد صحیح (مثل عمر مفید) - خالی یا نامعتبر به None تبدیل می‌شود"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


GENERAL_SCOPE_VALUES = ('general', 'general_kitchen_bar', 'general_other')


def _normalize_general_scope(raw_value):
    val = str(raw_value or '').strip()
    if not val:
        return ''
    if val in ('general_kitchen_bar', 'general_other'):
        return val
    return 'general'


def _resolve_product_sections(post_data, cafe, existing_product=None):
    """استخراج بخش‌های درخواست انتخاب‌شده برای محصول از داده‌های POST.

    خروجی ``(departments_or_None, usage_location_str, error_msg)``:
    - وقتی ``departments_or_None`` برابر ``None`` است یعنی بخش‌های فعلی محصول تغییر نکند.
    - برای حالت‌های «عمومی»: ``([], 'general' | 'general_kitchen_bar' | 'general_other', '')`` برمی‌گردد.
    - برای یک یا چند بخش: ``(depts, dept_name_or_'multi', '')`` برمی‌گردد.
    """
    has_marker = 'departments_present' in post_data
    has_depts = 'departments' in post_data
    raw_general = (
        post_data.getlist('usage_general')[-1]
        if hasattr(post_data, 'getlist') and post_data.getlist('usage_general')
        else post_data.get('usage_general')
    )
    general_scope = _normalize_general_scope(raw_general)

    if has_marker or has_depts:
        raw_ids = post_data.getlist('departments') if hasattr(post_data, 'getlist') else (post_data.get('departments') or [])
        if not isinstance(raw_ids, (list, tuple)):
            raw_ids = [raw_ids]
        dept_ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]

        if dept_ids:
            qs = Department.objects.filter(id__in=dept_ids, is_active=True)
            if cafe is not None:
                qs = qs.filter(cafe=cafe)
            depts = list(qs.order_by('name'))
            if not depts:
                return [], None, 'بخش‌های درخواست انتخاب‌شده معتبر نیستند.'
            usage_loc = depts[0].name[:20] if len(depts) == 1 else 'multi'
            return depts, usage_loc, ''

        if general_scope:
            return [], general_scope, ''

        return [], None, 'حداقل یک بخش درخواست (یا گزینه عمومی) را انتخاب کنید.'

    if 'usage_location' in post_data:
        val = (post_data.get('usage_location') or '').strip()
        if not val or val in GENERAL_SCOPE_VALUES:
            return [], (val or 'general'), ''
        dept_qs = Department.objects.filter(name=val)
        if cafe is not None:
            dept_qs = dept_qs.filter(cafe=cafe)
        dept = dept_qs.first()
        if dept:
            return [dept], dept.name[:20], ''
        return [], val[:20], ''

    if existing_product is not None:
        return None, existing_product.usage_location, ''
    return [], 'general', ''


def _selected_sections_for_form(product, departments_qs, form_data):
    """شناسهٔ بخش‌های انتخاب‌شده و دامنهٔ عمومی برای پیش‌پرکردن فرم محصول."""
    if form_data is not None:
        if 'departments_present' in form_data or 'departments' in form_data:
            raw_ids = form_data.getlist('departments') if hasattr(form_data, 'getlist') else (form_data.get('departments') or [])
            if not isinstance(raw_ids, (list, tuple)):
                raw_ids = [raw_ids]
            dept_ids = {int(x) for x in raw_ids if str(x).strip().isdigit()}
            if dept_ids:
                return dept_ids, ''
            raw_general = (
                form_data.getlist('usage_general')[-1]
                if hasattr(form_data, 'getlist') and form_data.getlist('usage_general')
                else form_data.get('usage_general')
            )
            return set(), _normalize_general_scope(raw_general)
        ul = (form_data.get('usage_location') or '').strip()
        if ul in GENERAL_SCOPE_VALUES:
            return set(), ul
        if ul:
            return {d.id for d in departments_qs if d.name == ul}, ''
        return set(), ''

    if product is not None:
        dept_ids = set(product.departments.values_list('id', flat=True))
        if dept_ids:
            return dept_ids, ''
        if not product.usage_location or product.usage_location in GENERAL_SCOPE_VALUES:
            return set(), (product.usage_location or 'general')
        mapped = Product.OLD_USAGE_KEY_MAP.get(product.usage_location, product.usage_location)
        matched = {
            d.id for d in departments_qs
            if d.name == product.usage_location or d.name == mapped
        }
        return matched, ''

    return set(), ''


def _render_product_form(request, product=None, mode='create', error='', form_data=None):
    """رندر مشترک فرم محصول"""
    cafe = get_cafe_from_request(request)
    categories = filter_by_cafe(Category.objects.all(), request)
    units = filter_by_cafe(Unit.objects.all(), request)
    departments_qs = list(Department.objects.filter(is_active=True, cafe=cafe).order_by('name')) if cafe else []
    selected_department_ids, selected_general_scope = _selected_sections_for_form(
        product, departments_qs, form_data
    )
    cogs_item = getattr(product, 'cogs_item', None) if product is not None else None
    return render(request, "product_form.html", {
        "product": product,
        "mode": mode,
        "categories": categories,
        "units": units,
        "departments_qs": departments_qs,
        "selected_department_ids": selected_department_ids,
        "selected_general_scope": selected_general_scope,
        "is_general_selected": bool(selected_general_scope),
        "departments": DEPARTMENT_CHOICES,
        "error": error,
        "form_data": form_data,
        "cafe_code": get_cafe_code(request) or 'local',
        "price_units": PRICE_UNIT_CHOICES,
        "cogs_item": cogs_item,
        "cogs_used_in_recipe": bool(cogs_item and cogs_item.is_used_in_recipe),
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def product_create(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        raise Http404('کافهٔ فعال یافت نشد')
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            return _render_product_form(request, error='نام محصول الزامی است.', form_data=request.POST)
        # Check for duplicate product name in same cafe
        existing = Product.objects.filter(name__iexact=name, cafe=cafe)
        if existing.exists():
            return _render_product_form(
                request,
                error=f'محصولی با نام «{name}» قبلاً ثبت شده است.',
                form_data=request.POST,
            )
        selected_depts, usage_loc, sec_error = _resolve_product_sections(request.POST, cafe)
        if sec_error:
            return _render_product_form(request, error=sec_error, form_data=request.POST)
        try:
            min_stock_val = int(request.POST.get("min_stock") or 0)
        except (ValueError, TypeError):
            return _render_product_form(request, error='حداقل موجودی نامعتبر است.', form_data=request.POST)
        if min_stock_val < 0:
            return _render_product_form(request, error='حداقل موجودی نمی‌تواند منفی باشد.', form_data=request.POST)

        cat_raw = (request.POST.get("category") or "").strip()
        unit_raw = (request.POST.get("unit") or "").strip()
        category = Category.objects.filter(id=int(cat_raw), cafe=cafe).first() if cat_raw.isdigit() else None
        unit = Unit.objects.filter(id=int(unit_raw), cafe=cafe).first() if unit_raw.isdigit() else None
        selected_product = Product.objects.create(
            name=name,
            category=category,
            unit=unit,
            min_stock=min_stock_val,
            shelf_life_days=_parse_int_or_none(request.POST.get("shelf_life_days")),
            usage_location=usage_loc,
            is_active=("is_active" in request.POST),
            cafe=cafe,
        )
        if selected_depts is not None:
            selected_product.departments.set(selected_depts)
        # قیمت مواد غذایی: هم روی محصول و هم روی ماده اولیه منو ثبت می‌شود
        _item, price_changed = _apply_product_price(selected_product, request)
        if price_changed:
            _snapshot_costs_after_price_change(request, cafe, selected_product.name)
        messages.success(request, f'محصول «{name}» ایجاد شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')

    return _render_product_form(request)


@custom_login_required
@role_required(["purchasing", "manager"])
def product_update(request, product_id):
    """ویرایش اطلاعات پایه محصول (نام، دسته‌بندی، واحد، حداقل موجودی، ...)"""
    cafe = get_cafe_from_request(request)
    product = get_scoped_object_or_404(Product, request, id=product_id)
    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip()
        if not new_name:
            return _render_product_form(request, product=product, mode='edit_product',
                                        error='نام محصول الزامی است.', form_data=request.POST)
        existing = Product.objects.filter(name__iexact=new_name, cafe=cafe).exclude(id=product.id)
        if existing.exists():
            return _render_product_form(request, product=product, mode='edit_product',
                                        error=f'محصولی با نام «{new_name}» قبلاً ثبت شده است.', form_data=request.POST)
        selected_depts, usage_loc, sec_error = _resolve_product_sections(
            request.POST, cafe, existing_product=product
        )
        if sec_error:
            return _render_product_form(request, product=product, mode='edit_product',
                                        error=sec_error, form_data=request.POST)
        try:
            min_stock_raw = request.POST.get("min_stock")
            min_stock_val = int(min_stock_raw) if (min_stock_raw is not None and str(min_stock_raw).strip() != "") else product.min_stock
        except (ValueError, TypeError):
            return _render_product_form(request, product=product, mode='edit_product',
                                        error='حداقل موجودی نامعتبر است.', form_data=request.POST)
        if min_stock_val < 0:
            return _render_product_form(request, product=product, mode='edit_product',
                                        error='حداقل موجودی نمی‌تواند منفی باشد.', form_data=request.POST)

        cat_raw = (request.POST.get("category") or "").strip()
        unit_raw = (request.POST.get("unit") or "").strip()
        product.name = new_name
        if cat_raw.isdigit():
            product.category = Category.objects.filter(id=int(cat_raw), cafe=cafe).first() or product.category
        if unit_raw.isdigit():
            product.unit = Unit.objects.filter(id=int(unit_raw), cafe=cafe).first() or product.unit
        product.min_stock = min_stock_val
        product.shelf_life_days = _parse_int_or_none(request.POST.get("shelf_life_days"))
        product.usage_location = usage_loc
        product.description = request.POST.get("description", product.description)
        if "is_active_present" in request.POST:
            product.is_active = ("is_active" in request.POST)
        if not product.is_complete:
            product.is_complete = True
        product.save()
        if selected_depts is not None:
            product.departments.set(selected_depts)
        # قیمت واحد کوچک‌ترین واحد — هم‌گام با ماده اولیه منو (در صورت وجود)
        _item, price_changed = _apply_product_price(product, request)
        if _item is None:
            from ..services.product_sync import sync_product
            sync_product(product)
        if price_changed:
            _snapshot_costs_after_price_change(request, cafe, product.name)
        messages.success(request, 'محصول با موفقیت ویرایش شد.')
        return redirect("product_list", cafe_code=get_cafe_code(request) or 'local')
    return _render_product_form(request, product=product, mode='edit_product')


@custom_login_required
@role_required(["purchasing", "manager"])
def product_toggle_active(request, product_id):
    """تغییر سریع وضعیت فعال/غیرفعال محصول در سفارش‌گذاری."""
    cafe_code = get_cafe_code(request) or 'local'
    product = get_scoped_object_or_404(Product, request, id=product_id)
    if request.method == "POST":
        product.is_active = not product.is_active
        product.save(update_fields=['is_active', 'updated_at'])
        state_label = 'فعال' if product.is_active else 'غیرفعال'
        messages.success(request, f'محصول «{product.name}» در سفارش‌گذاری {state_label} شد.')
    from urllib.parse import urlencode
    kept_params = {
        key: request.POST.get(f'filter_{key}') or request.GET.get(key)
        for key in ('search', 'category', 'department', 'is_active', 'pricing', 'recipe')
        if (request.POST.get(f'filter_{key}') or request.GET.get(key))
    }
    target = reverse("product_list", kwargs={"cafe_code": cafe_code})
    if kept_params:
        target = f"{target}?{urlencode(kept_params)}"
    return redirect(target)


@custom_login_required
@role_required(["purchasing", "manager"])
def product_delete(request, product_id):
    cafe_code = get_cafe_code(request) or 'local'
    product = get_scoped_object_or_404(Product, request, id=product_id)
    if request.method == "POST":
        try:
            product.delete()
            messages.success(request, f'محصول «{product.name}» با موفقیت حذف شد.')
        except ProtectedError:
            messages.error(
                request,
                f'امکان حذف «{product.name}» وجود ندارد؛ این کالا در درخواست‌ها، فاکتورها یا موجودی انبار استفاده شده است.',
            )
        return redirect("product_list", cafe_code=cafe_code)
    return render(request, "product_confirm_delete.html", {"product": product})


@custom_login_required
@role_required(["purchasing", "manager", "finance_manager", "accounting", "ceo"])
def search_supplier_products(request):
    """Search for products used in previous invoices for a specific supplier."""
    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'results': []}, status=403)
    supplier_id = (request.GET.get('supplier_id') or '').strip()
    query = request.GET.get('q', '').strip()

    if not supplier_id.isdigit():
        return JsonResponse({'results': []})

    items = InvoiceItem.objects.filter(
        invoice__cafe=cafe,
        invoice__supplier_id=int(supplier_id),
        description__icontains=query
    ).values_list('description', flat=True).distinct()[:10]

    return JsonResponse({'results': list(items)})


def _filter_products_for_user(products, user, cafe):
    """فیلتر کردن محصولات قابل مشاهده بر اساس بخش کاربر و گیت رسپی."""
    if not bypasses_recipe_gate(user):
        # درخواست‌کننده کالای غذاییِ بدون رسپی را نمی‌بیند — کالاهای غیرغذایی
        # (شوینده، مصرفی، ...) و کالاهای داخل رسپی دست‌نخورده می‌مانند.
        products = filter_requestable_products(products, cafe=cafe)

    user_role = user.role
    if user_role not in PRODUCT_MANAGER_ROLES:
        has_any_dept = models.Exists(
            Product.departments.through.objects.filter(product_id=models.OuterRef('pk'))
        )
        if user_role == 'requester':
            dep = user.department
            if dep:
                dep_name = dep.name
                old_key_map = {
                    'آشپزخانه': 'kitchen', 'بار': 'bar', 'کانتر': 'counter',
                    'صندوق': 'cashier', 'تاسیسات': 'facilities',
                }
                old_key = old_key_map.get(dep_name, None)
                matches_dept = models.Exists(
                    Product.departments.through.objects.filter(
                        product_id=models.OuterRef('pk'),
                        department_id=dep.id,
                    )
                )
                legacy_match = models.Q(usage_location='general') | models.Q(usage_location=dep_name)
                if getattr(dep, 'is_recipe_department', False):
                    legacy_match = legacy_match | models.Q(usage_location='general_kitchen_bar')
                else:
                    legacy_match = legacy_match | models.Q(usage_location='general_other')
                if old_key:
                    legacy_match = legacy_match | models.Q(usage_location=old_key)
                products = products.filter(matches_dept | (~has_any_dept & legacy_match))
            else:
                products = products.filter(~has_any_dept & models.Q(usage_location='general'))
        else:
            products = products.filter(
                ~has_any_dept & (models.Q(usage_location=user_role) | models.Q(usage_location='general'))
            )
    return products


def _order_products_by_query(products, query: str):
    """مرتب‌سازی نتایج جستجو: ابتدا تطابق دقیق، سپس کالاهایی که با عبارت شروع می‌شوند، و بعد سایر موارد."""
    if not query:
        return products.order_by('name')
    return products.annotate(
        starts_with_rank=models.Case(
            models.When(name__iexact=query, then=models.Value(0)),
            models.When(name__istartswith=query, then=models.Value(1)),
            default=models.Value(2),
            output_field=models.IntegerField(),
        )
    ).order_by('starts_with_rank', 'name')


def _serialize_product_suggestions(products):
    results = []
    for product in products:
        category_display = ''
        if product.category:
            if product.category.parent:
                category_display = f"{product.category.parent.name} > {product.category.name}"
            else:
                category_display = product.category.name

        results.append({
            'id': product.id,
            'name': product.name,
            'category': category_display,
            'category_id': product.category_id,
            'label': product.name,
            'unit': product.unit.name if product.unit else '',
        })
    return results


@custom_login_required
def search_products_autocomplete(request):
    """Autocomplete API for products (searches product name only, prioritizing prefix matches)."""
    query = request.GET.get('q', '').strip()

    if not query or len(query) < 2:
        return JsonResponse({'results': []})

    cafe = get_cafe_from_request(request)
    products = Product.objects.filter(
        name__icontains=query,
        is_active=True,
        # کالاهای ناقص (تازه ساخته‌شده از فرم درخواست) تا تکمیل اطلاعات در مدیریت محصولات،
        # در پیشنهادهای درخواست‌های بعدی نمایش داده نمی‌شوند
        is_complete=True,
    ).select_related('category', 'category__parent', 'unit')
    if cafe:
        products = products.filter(cafe=cafe)

    products = _filter_products_for_user(products, request.user, cafe)
    products = _order_products_by_query(products, query)[:15]

    return JsonResponse({'results': _serialize_product_suggestions(products)})


@custom_login_required
def category_products_api(request: HttpRequest, category_id: int = None) -> JsonResponse:
    """API محصولات فعالِ یک دسته‌بندی (یا همه کالاها) برای فرم درخواست خرید."""
    if category_id is None:
        category_id = request.GET.get('category_id', '')
    query = request.GET.get('q', '').strip()

    cafe = get_cafe_from_request(request)
    products = Product.objects.filter(is_active=True, is_complete=True).select_related(
        'category', 'category__parent', 'unit')
    if cafe:
        products = products.filter(cafe=cafe)

    is_all = str(category_id) == 'all'
    if not is_all:
        if category_id and str(category_id).isdigit():
            # محصولات دسته انتخاب‌شده + زیردسته‌های آن (محدود به کافه جاری)
            cat = filter_by_cafe(Category.objects.filter(id=int(category_id), is_active=True), request).first()
            if not cat:
                return JsonResponse({'results': []})
            cat_ids = [cat.id] + list(cat.subcategories.filter(is_active=True).values_list('id', flat=True))
            products = products.filter(category_id__in=cat_ids)
        else:
            return JsonResponse({'results': []})

    if query:
        products = products.filter(name__icontains=query)

    products = _filter_products_for_user(products, request.user, cafe)
    limit = 500 if (is_all and not query) else 50
    products = _order_products_by_query(products, query)[:limit]

    return JsonResponse({'results': _serialize_product_suggestions(products)})


@custom_login_required
def check_product_availability(request: HttpRequest) -> JsonResponse:
    """بررسی موجودی انبار برای محصول انتخاب‌شده هنگام ثبت درخواست کالا."""
    product_id_raw = (request.GET.get('product_id') or '').strip()
    if not product_id_raw.isdigit():
        return JsonResponse({'error': 'محصول یافت نشد'}, status=404)

    qty = _parse_decimal(request.GET.get('qty', '') or 0)
    if qty is None or qty < 0 or not qty.is_finite():
        return JsonResponse({'error': 'مقدار نامعتبر است'}, status=400)

    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'error': 'کافه یافت نشد'}, status=404)

    product = get_scoped_object_or_404(Product, request, id=int(product_id_raw))

    from ..services.units import convert_quantity_to_smallest_unit, units_are_compatible

    stock_rows = list(
        StockItem.objects.filter(
            product=product, quantity__gt=0, warehouse__cafe=cafe
        ).select_related('warehouse', 'unit')
    )

    total_qty = sum((s.quantity for s in stock_rows), Decimal('0'))
    unit_label = (
        rows_unit
        if (rows_unit := (stock_rows[0].unit.name if stock_rows and stock_rows[0].unit else ''))
        else (product.unit.name if product.unit else '')
    )
    req_unit = (request.GET.get('unit') or '').strip()
    warehouses = sorted({s.warehouse.name for s in stock_rows if s.warehouse})
    if req_unit:
        req_smallest, _ = convert_quantity_to_smallest_unit(qty, req_unit)
        compatible_smallest = Decimal('0')
        for s in stock_rows:
            s_unit = (s.unit.name if s.unit else '') or unit_label
            if s_unit and units_are_compatible(s_unit, req_unit):
                s_smallest, _ = convert_quantity_to_smallest_unit(s.quantity, s_unit)
                if s_smallest is not None:
                    compatible_smallest += s_smallest
            elif not s_unit:
                compatible_smallest += s.quantity
        available = qty > 0 and req_smallest is not None and compatible_smallest >= req_smallest
    else:
        available = qty > 0 and total_qty >= qty

    return JsonResponse({
        'product_id': product.id,
        'qty': _fmt2(qty),
        'available': available,
        'stock_trade': _fmt2(total_qty),
        'unit_label': unit_label,
        'warehouses': warehouses,
    }, json_dumps_params={'ensure_ascii': False})


@custom_login_required
@role_required(["kitchen", "bar", "counter", "store", "cashier", "facilities", "purchasing", "warehouse", "manager", "ceo", "requester"])
def quick_add_product(request):
    """Create a new Product on-the-fly from the request form.

    POST JSON body: {"name": "...", "unit": "...", "description": "..."}
    Returns JSON: {"success": true, "product": {"id": ..., "name": "...", "unit": "..."}}

    کالای ساخته‌شده همیشه در دستهٔ «تحقیق و توسعه» کافه قرار می‌گیرد تا مدیریت
    آن را بررسی و در صورت تایید به دستهٔ واقعی منتقل کند.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'درخواست نامعتبر است'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'success': False, 'error': 'درخواست نامعتبر است'}, status=400)

    name = str(data.get('name') or '').strip()
    unit_name = str(data.get('unit') or 'عدد').strip() or 'عدد'
    usage_location = str(data.get('usage_location') or '').strip()
    description = str(data.get('description') or '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'نام کالا الزامی است'})

    # توضیحات برای کالای جدید اجباری است
    if not description:
        return JsonResponse({
            'success': False,
            'error': 'توضیحات اجباری است — لطفاً توضیح کوتاهی بنویس.',
            'error_code': 'description_required',
        })

    cafe = get_cafe_from_request(request)
    if cafe is None:
        return JsonResponse({'success': False, 'error': 'کافه فعال یافت نشد'}, status=400)

    # Auto-detect usage_location from user's department if not provided
    if not usage_location:
        if request.user.role == 'requester' and request.user.department:
            usage_location = request.user.department.name
        else:
            usage_location = 'general'

    # Return existing product if same name already exists
    existing_qs = Product.objects.filter(name__iexact=name)
    if cafe:
        existing_qs = existing_qs.filter(cafe=cafe)
    if existing_qs.exists():
        p = existing_qs.select_related('category', 'unit').first()
        inactive_err = inactive_product_error(p)
        if inactive_err:
            return JsonResponse({'success': False, 'error': inactive_err})
        if not bypasses_recipe_gate(request.user):
            gate_err = recipe_gate_error(p, cafe=cafe)
            if gate_err:
                return JsonResponse({'success': False, 'error': gate_err})
        if p.category_id is None and not p.is_complete:
            assign_rd_category(p, cafe=cafe)
        return JsonResponse({'success': True, 'product': {
            'id': p.id,
            'name': p.name,
            'unit': p.unit.name if p.unit else unit_name,
            'is_complete': p.is_complete,
            'category_id': p.category_id,
            'category_name': p.category.name if p.category else '',
        }})

    # Find or create the unit
    unit_obj = None
    if unit_name:
        unit_obj = Unit.objects.filter(name=unit_name, cafe=cafe).first()
        if not unit_obj:
            try:
                unit_obj, _ = Unit.objects.get_or_create(name=unit_name, cafe=cafe)
            except IntegrityError:
                unit_obj = Unit.objects.filter(name=unit_name, cafe=cafe).first()

    try:
        product = Product.objects.create(
            name=name,
            unit=unit_obj,
            usage_location=usage_location[:20] if usage_location else 'general',
            description=description,
            # کالای جدید ناقص است تا مدیریت محصولات فیلدهای دیگر (دسته‌بندی، برند، ...) را تکمیل کند
            is_complete=False,
            cafe=cafe,
            is_active=True,
        )
    except IntegrityError:
        p = Product.objects.filter(name__iexact=name, cafe=cafe).select_related('category', 'unit').first()
        if p is not None:
            inactive_err = inactive_product_error(p)
            if inactive_err:
                return JsonResponse({'success': False, 'error': inactive_err})
            if not bypasses_recipe_gate(request.user):
                gate_err = recipe_gate_error(p, cafe=cafe)
                if gate_err:
                    return JsonResponse({'success': False, 'error': gate_err})
            return JsonResponse({'success': True, 'product': {
                'id': p.id,
                'name': p.name,
                'unit': p.unit.name if p.unit else unit_name,
                'is_complete': p.is_complete,
                'category_id': p.category_id,
                'category_name': p.category.name if p.category else '',
            }})
        return JsonResponse({'success': False, 'error': 'این کالا قبلاً ثبت شده است'}, status=409)
    if usage_location and usage_location != 'general':
        dept_qs = Department.objects.filter(name=usage_location, is_active=True)
        if cafe:
            dept_qs = dept_qs.filter(cafe=cafe)
        matched_dept = dept_qs.first()
        if matched_dept is not None:
            product.departments.add(matched_dept)

    # دستهٔ انتخابیِ کاربر عمداً نادیده گرفته می‌شود: هر کالایی که درخواست‌کننده
    # بیرون از فهرست تعریف می‌کند، تا بررسی مدیریت در «تحقیق و توسعه» می‌ماند.
    rd_category = assign_rd_category(product, cafe=cafe)

    return JsonResponse({'success': True, 'product': {
        'id': product.id,
        'name': product.name,
        'unit': product.unit.name if product.unit else unit_name,
        'is_complete': product.is_complete,
        'description': product.description,
        'category_id': rd_category.id if rd_category else None,
        'category_name': rd_category.name if rd_category else '',
    }})
