from __future__ import annotations

from ..models import Category
from ..models import Department
from ..models import Product
from ..models import Unit
from .helpers import custom_login_required
from .helpers import filter_by_cafe
from .helpers import get_cafe_from_request
from .helpers import get_cafe_redirect_url
from .helpers import get_scoped_object_or_404
from .helpers import role_required
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.http import JsonResponse
from django.shortcuts import render
import json


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_list(request):
    units = filter_by_cafe(Unit.objects.all(), request)
    return render(request, "unit_list.html", {"units": units})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_create(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        symbol = (request.POST.get("symbol") or "").strip()
        if not name:
            messages.error(request, 'نام واحد الزامی است.')
            return render(request, "unit_form.html", {"error": 'نام واحد الزامی است.'})
        try:
            Unit.objects.create(name=name, symbol=symbol, cafe=get_cafe_from_request(request))
            return get_cafe_redirect_url("unit_list", request)
        except (IntegrityError, ValueError):
            err = f'واحد «{name}» از قبل وجود دارد یا مقدار آن نامعتبر است.'
            messages.error(request, err)
            return render(request, "unit_form.html", {"error": err})
    return render(request, "unit_form.html")


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_update(request, unit_id):
    # فقط واحد کافه جاری قابل ویرایش است — واحد کافه دیگر ۴۰۴ میشود
    unit = get_scoped_object_or_404(Unit, request, id=unit_id)
    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip()
        if not new_name:
            err = 'نام واحد الزامی است.'
            messages.error(request, err)
            return render(request, "unit_form.html", {"unit": unit, "error": err})
        unit.name = new_name
        unit.symbol = (request.POST.get("symbol") or "").strip()
        try:
            unit.save()
            return get_cafe_redirect_url("unit_list", request)
        except (IntegrityError, ValueError):
            err = f'واحد «{new_name}» از قبل وجود دارد یا مقدار آن نامعتبر است.'
            messages.error(request, err)
            return render(request, "unit_form.html", {"unit": unit, "error": err})
    return render(request, "unit_form.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def unit_delete(request, unit_id):
    unit = get_scoped_object_or_404(Unit, request, id=unit_id)
    if request.method == "POST":
        unit.delete()
        return get_cafe_redirect_url("unit_list", request)
    return render(request, "unit_confirm_delete.html", {"unit": unit})


@custom_login_required
@role_required(["purchasing", "manager"])
def category_list(request):
    categories = filter_by_cafe(Category.objects.all(), request)
    return render(request, "category_list.html", {"categories": categories})


def _category_parent_choices(cafe, exclude_id=None):
    """دسته‌های والد قابل انتخاب (همه کافه جاری، بدون خود دسته)"""
    parents = Category.objects.filter(parent__isnull=True, is_active=True)
    if exclude_id:
        parents = parents.exclude(id=exclude_id)
    if cafe:
        parents = parents.filter(cafe=cafe)
    return parents


@custom_login_required
@role_required(["purchasing", "manager"])
def category_create(request):
    """ایجاد دسته‌بندی جدید — شامل تعیین «نیازمند استفاده در رسپی»"""
    cafe = get_cafe_from_request(request)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        error = None
        if not name:
            error = 'نام دسته‌بندی الزامی است.'
        elif Category.objects.filter(name__iexact=name, cafe=cafe).exists():
            error = f'دسته‌بندی «{name}» در این کافه از قبل وجود دارد.'
        if error:
            return render(request, "category_form.html", {
                "category": None,
                "parent_categories": _category_parent_choices(cafe),
                "error": error,
                "form_data": request.POST,
            })
        parent_id = (request.POST.get("parent") or "").strip()
        try:
            parent_obj = None
            if parent_id:
                parent_obj = Category.objects.filter(id=int(parent_id), cafe=cafe).first()
            category = Category.objects.create(
                name=name,
                description=(request.POST.get("description") or "").strip(),
                parent=parent_obj,
                requires_recipe=request.POST.get("requires_recipe") == "on",
                is_active=True,
                cafe=cafe,
            )
        except (IntegrityError, ValueError):
            return render(request, "category_form.html", {
                "category": None,
                "parent_categories": _category_parent_choices(cafe),
                "error": f'خطا در ایجاد دسته‌بندی «{name}» — نام تکراری یا مقدار نامعتبر است.',
                "form_data": request.POST,
            })
        messages.success(request, f'دسته‌بندی «{category.name}» ایجاد شد.')
        return get_cafe_redirect_url("category_list", request)

    return render(request, "category_form.html", {
        "category": None,
        "parent_categories": _category_parent_choices(cafe),
        "form_data": {"requires_recipe": "on"},
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def category_update(request, category_id):
    cafe = get_cafe_from_request(request)
    # فقط دستهٔ کافه جاری قابل ویرایش است — دستهٔ کافه دیگر ۴۰۴ میشود
    category = get_scoped_object_or_404(Category, request, id=category_id)
    if request.method == "POST":
        new_name = (request.POST.get("name") or "").strip() or category.name
        new_desc = request.POST.get("description", category.description)
        parent_raw = (request.POST.get("parent") or "").strip()
        new_parent = None
        if parent_raw:
            if not parent_raw.isdigit():
                messages.error(request, 'دسته والد نامعتبر است.')
                return get_cafe_redirect_url("category_list", request)
            new_parent = Category.objects.filter(id=int(parent_raw), cafe=cafe).first()
            # جلوگیری از چرخه در سلسله‌مراتب دسته‌بندی‌ها
            cursor = new_parent
            visited = {category.id}
            while cursor is not None:
                if cursor.id in visited:
                    messages.error(request, 'انتخاب این دسته به عنوان والد باعث ایجاد چرخه می‌شود.')
                    return get_cafe_redirect_url("category_list", request)
                visited.add(cursor.id)
                cursor = cursor.parent
        category.name = new_name
        category.description = new_desc
        category.parent = new_parent
        category.requires_recipe = request.POST.get("requires_recipe") == "on"
        try:
            category.save()
        except (IntegrityError, ValueError):
            err = f'خطا در ذخیره دسته‌بندی «{new_name}» — نام تکراری یا مقدار نامعتبر است.'
            messages.error(request, err)
            return render(request, "category_form.html", {
                "category": category,
                "parent_categories": _category_parent_choices(cafe, exclude_id=category.id),
                "error": err,
            })
        return get_cafe_redirect_url("category_list", request)

    # Get all parent categories except the current one and its descendants
    parent_categories = Category.objects.filter(
        parent__isnull=True,
        is_active=True
    ).exclude(id=category_id)
    if cafe:
        parent_categories = parent_categories.filter(cafe=cafe)

    return render(request, "category_form.html", {
        "category": category,
        "parent_categories": parent_categories
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def category_delete(request, category_id):
    category = get_scoped_object_or_404(Category, request, id=category_id)
    has_products = Product.objects.filter(category=category).exists()
    if request.method == "POST":
        if category.requires_recipe or has_products:
            messages.error(
                request,
                f'امکان حذف دسته‌بندی «{category.name}» وجود ندارد؛ '
                'این دسته دارای محصول متصل یا قانون اجبار رسپی است و حذف آن باعث دور زدن کنترل رسپی می‌شود.',
            )
            return get_cafe_redirect_url("category_list", request)
        try:
            category.delete()
            messages.success(request, f'دسته‌بندی «{category.name}» حذف شد.')
        except ProtectedError:
            messages.error(request, f'امکان حذف دسته‌بندی «{category.name}» وجود ندارد زیرا در رکوردهای دیگر استفاده شده است.')
        return get_cafe_redirect_url("category_list", request)
    return render(request, "category_confirm_delete.html", {
        "category": category,
        "has_products": has_products,
        "requires_recipe": category.requires_recipe,
    })


@custom_login_required
def search_categories_autocomplete(request):
    """Autocomplete API for categories with subcategories"""
    query = request.GET.get('q', '').strip()
    parent_id = request.GET.get('parent_id')
    
    if not query or len(query) < 2:
        return JsonResponse({'results': []})
    
    cafe = get_cafe_from_request(request)
    categories = Category.objects.filter(
        name__icontains=query,
        is_active=True
    ).select_related('parent')
    if cafe:
        categories = categories.filter(cafe=cafe)
    
    if parent_id:
        categories = categories.filter(parent_id=parent_id)
    
    categories = categories[:15]
    
    results = []
    for cat in categories:
        display_name = cat.full_path if cat.parent else cat.name
        results.append({
            'id': cat.id,
            'name': cat.name,
            'parent_id': cat.parent_id,
            'full_path': display_name,
            'label': display_name
        })
    
    return JsonResponse({'results': results})


@custom_login_required
@role_required(["purchasing", "manager"])
def quick_add_department(request):
    """Create a new Department on-the-fly from the user or product form.

    POST JSON body: {"name": "..."}
    Returns JSON: {"success": true, "department": {"id": ..., "name": "..."}}
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

    if not name:
        return JsonResponse({'success': False, 'error': 'نام بخش الزامی است'})

    cafe = get_cafe_from_request(request)

    # بدون کافه جاری امکان ساخت بخش نیست (بخش سراسری وجود ندارد)
    if not cafe:
        return JsonResponse({'success': False, 'error': 'ابتدا کافه را انتخاب کنید'})

    # Return existing department if same name already exists
    existing_qs = Department.objects.filter(name__iexact=name, cafe=cafe)
    if existing_qs.exists():
        d = existing_qs.first()
        if not d.is_active:
            d.is_active = True
            d.save(update_fields=['is_active'])
        return JsonResponse({'success': True, 'department': {
            'id': d.id,
            'name': d.name,
        }})

    department = Department.objects.create(
        name=name,
        cafe=cafe,
        is_active=True,
    )

    return JsonResponse({'success': True, 'department': {
        'id': department.id,
        'name': department.name,
    }})
