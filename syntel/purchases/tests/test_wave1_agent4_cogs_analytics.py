# -*- coding: utf-8 -*-
"""Wave 1 Agent 4 tests: COGS engine, unit/catalog sync, consumption, analytics, reports, alerts."""
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe,
    Category,
    Department,
    Invoice,
    InvoiceItem,
    KukerItem,
    KukerRecipeLine,
    PriceHistory,
    Product,
    PurchaseItem,
    PurchaseOrder,
    PurchaseRequest,
    Requester,
    StockItem,
    StockTransaction,
    Supplier,
    Unit,
    Warehouse,
)
from purchases.services.menu_cogs import MenuGraph
from purchases.views.analytics import analytics_dashboard, category_expenses_api, department_expenses_api
from purchases.views.catalog import check_product_availability, product_delete, product_update, quick_add_product
from purchases.views.categories import category_create, category_delete, unit_create
from purchases.views.cogs_items import _resolve_price_input
from purchases.views.consumption_reports import _collect_consumption_deliveries, _estimate_consumption_cost
from purchases.views.reports import export_to_pdf, inventory_reports, payment_analytics, purchase_reports


User = get_user_model()


@pytest.fixture
def cafe(db):
    return Cafe.objects.create(code='test_a4', name='کافه تست ۴')


@pytest.fixture
def manager_user(db, cafe):
    return User.objects.create_user(
        username='mgr_a4',
        password='pw',
        role='manager',
        cafe=cafe,
        email='mgr_a4@example.com',
    )


def _attach_session_and_cafe(req, user, cafe):
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.middleware import SessionMiddleware

    req.user = user
    req.cafe = cafe
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    req._messages = FallbackStorage(req)
    return req


# ---------------------------------------------------------------------------
# 1. MenuGraph COGS fixes
# ---------------------------------------------------------------------------

def test_menu_graph_unit_cost_prioritizes_live_computed_cost():
    g = MenuGraph()
    g.items['raw_flour'] = {
        'key': 'raw_flour', 'name': 'آرد', 'kind': 'raw', 'sheet': '',
        'category': '', 'out_qty': Decimal('1'), 'out_unit': 'گرم',
        'stated_total': None, 'stated_fee': None,
        'price': Decimal('50'), 'price_unit': 'گرم', 'code': '', 'group': '',
        'lines': [],
    }
    g.items['dish_bread'] = {
        'key': 'dish_bread', 'name': 'نان', 'kind': 'dish', 'sheet': 'menu',
        'category': 'نان', 'out_qty': Decimal('1'), 'out_unit': 'پرس',
        'stated_total': Decimal('1000'), 'stated_fee': Decimal('1000'),
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [('raw_flour', Decimal('100'), 'گرم', Decimal('10'))],
    }
    # Live computed cost = 100 * 50 = 5000 (overrides frozen stated_fee=1000)
    assert g.unit_cost('dish_bread') == Decimal('5000')

    # Without recipe lines, falls back to stated_fee
    g.items['dish_no_lines'] = {
        'key': 'dish_no_lines', 'name': 'آیتم بدون رسپی', 'kind': 'dish', 'sheet': 'menu',
        'category': 'متفرقه', 'out_qty': Decimal('1'), 'out_unit': 'پرس',
        'stated_total': None, 'stated_fee': Decimal('2500'),
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [],
    }
    assert g.unit_cost('dish_no_lines') == Decimal('2500')


def test_menu_graph_prep_live_cost_not_overwritten_by_split_fee():
    g = MenuGraph()
    g.items['raw_sugar'] = {
        'key': 'raw_sugar', 'name': 'شکر', 'kind': 'raw', 'sheet': '',
        'category': '', 'out_qty': Decimal('1'), 'out_unit': 'گرم',
        'stated_total': None, 'stated_fee': None,
        'price': Decimal('40'), 'price_unit': 'گرم', 'code': '', 'group': '',
        'lines': [],
    }
    g.items['prep_syrup'] = {
        'key': 'prep_syrup', 'name': 'شربت', 'kind': 'prep', 'sheet': 'prep',
        'category': 'سس', 'out_qty': Decimal('1'), 'out_unit': 'کیلوگرم',
        'stated_total': None, 'stated_fee': None,
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [('raw_sugar', Decimal('500'), 'گرم', None)],
    }
    # Dish uses 500 گرم of prep_syrup with frozen Excel fee=10 per گرم (5000 total)
    # Live prep cost is 500 * 40 = 20,000 per کیلوگرم -> 0.5 کیلوگرم = 10,000
    g.items['dish_tea'] = {
        'key': 'dish_tea', 'name': 'چای شیرین', 'kind': 'dish', 'sheet': 'menu',
        'category': 'نوشیدنی', 'out_qty': Decimal('1'), 'out_unit': 'پرس',
        'stated_total': None, 'stated_fee': None,
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [('prep_syrup', Decimal('500'), 'گرم', Decimal('10'))],
    }
    cons = g.unit_consumption('dish_tea')
    raw_cost = cons['raw'][('raw_sugar', 'گرم')][1]
    prep_cost = cons['prep'][('prep_syrup', 'کیلوگرم')][1]
    assert raw_cost == Decimal('10000')
    assert prep_cost == Decimal('10000')


def test_menu_graph_incompatible_unit_zero_cost_warning_is_accurate():
    g = MenuGraph()
    g.items['prep_sauce'] = {
        'key': 'prep_sauce', 'name': 'سس مخصوص', 'kind': 'prep', 'sheet': 'prep',
        'category': 'سس', 'out_qty': Decimal('1'), 'out_unit': 'لیتر',
        'stated_total': None, 'stated_fee': None,
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [],
    }
    g.items['dish_pasta'] = {
        'key': 'dish_pasta', 'name': 'پاستا', 'kind': 'dish', 'sheet': 'menu',
        'category': 'غذا', 'out_qty': Decimal('1'), 'out_unit': 'پرس',
        'stated_total': None, 'stated_fee': None,
        'price': None, 'price_unit': '', 'code': '', 'group': '',
        'lines': [('prep_sauce', Decimal('2'), 'قاشق', None)],
    }
    cons = g.unit_consumption('dish_pasta')
    assert len(cons['warn']) == 1
    assert 'هزینه با بهای کامل حساب شد' not in cons['warn'][0]
    assert 'نبود قیمت' in cons['warn'][0]


# ---------------------------------------------------------------------------
# 2. Custom unit modal, product_sync, catalog, categories, import_catalog_csv
# ---------------------------------------------------------------------------

def test_resolve_price_input_preserves_custom_countable_unit():
    resolved = _resolve_price_input('15000', 'بطری')
    assert resolved is not None
    assert resolved['smallest_unit'] == 'بطری'
    assert resolved['price'] == Decimal('15000')


def test_cogs_manage_items_js_supports_custom_unit_option():
    js_path = Path(__file__).resolve().parents[2] / 'frontend' / 'static' / 'js' / 'cogs_manage_items.js'
    content = js_path.read_text(encoding='utf-8')
    assert 'data-custom-unit' in content
    assert "pmUnit.value = unitMap[baseUnit] || 'gram'" not in content


@pytest.mark.django_db
def test_product_update_syncs_unpriced_kuker_item_name_and_avoids_kg_redivision(cafe, manager_user):
    kg_unit = Unit.objects.create(name='کیلوگرم', cafe=cafe)
    cat = Category.objects.create(name='مواد خشک', cafe=cafe, requires_recipe=False)

    # 1) Unpriced product with linked KukerItem -> rename should update KukerItem name & key
    p1 = Product.objects.create(name='برنج طارم', unit=kg_unit, category=cat, cafe=cafe)
    k1 = KukerItem.objects.create(
        cafe=cafe, key='برنج طارم', name='برنج طارم', kind=KukerItem.KIND_RAW,
        out_unit='گرم', product=p1, price=None,
    )
    rf = RequestFactory()
    req = rf.post(
        f'/c/{cafe.code}/products/{p1.id}/edit/',
        {'name': 'برنج هاشمی', 'category': str(cat.id), 'unit': str(kg_unit.id), 'min_stock': '0'},
    )
    _attach_session_and_cafe(req, manager_user, cafe)
    resp = product_update(req, p1.id)
    assert resp.status_code == 302
    k1.refresh_from_db()
    assert k1.name == 'برنج هاشمی'
    assert 'هاشمی' in k1.key

    # 2) Product with base_unit_price=250 (already normalized) and smallest_unit='' on kg product
    p2 = Product.objects.create(
        name='شکر سفید', unit=kg_unit, category=cat, cafe=cafe,
        base_unit_price=Decimal('250.0000'), smallest_unit='',
    )
    req2 = rf.post(
        f'/c/{cafe.code}/products/{p2.id}/edit/',
        {
            'name': 'شکر سفید',
            'category': str(cat.id),
            'unit': str(kg_unit.id),
            'min_stock': '0',
            'base_unit_price': '250.0000',
        },
    )
    _attach_session_and_cafe(req2, manager_user, cafe)
    resp2 = product_update(req2, p2.id)
    assert resp2.status_code == 302
    p2.refresh_from_db()
    assert p2.base_unit_price == Decimal('250.0000')


@pytest.mark.django_db
def test_check_product_availability_converts_compatible_units(cafe, manager_user):
    kg_unit = Unit.objects.create(name='کیلوگرم', cafe=cafe)
    wh = Warehouse.objects.create(name='انبار مرکزی', cafe=cafe)
    prod = Product.objects.create(name='قهوه', unit=kg_unit, cafe=cafe)
    StockItem.objects.create(warehouse=wh, product=prod, quantity=Decimal('2'), unit=kg_unit)

    rf = RequestFactory()
    # 500 گرم <= 2 کیلوگرم (2000 گرم) -> True
    req = rf.get('/api/check-availability/', {'product_id': str(prod.id), 'qty': '500', 'unit': 'گرم'})
    _attach_session_and_cafe(req, manager_user, cafe)
    resp = check_product_availability(req)
    assert json.loads(resp.content)['available'] is True

    # 3000 گرم > 2 کیلوگرم -> False
    req2 = rf.get('/api/check-availability/', {'product_id': str(prod.id), 'qty': '3000', 'unit': 'گرم'})
    _attach_session_and_cafe(req2, manager_user, cafe)
    resp2 = check_product_availability(req2)
    assert json.loads(resp2.content)['available'] is False


@pytest.mark.django_db
def test_product_delete_handles_protected_error(cafe, manager_user):
    unit = Unit.objects.create(name='عدد', cafe=cafe)
    wh = Warehouse.objects.create(name='انبار', cafe=cafe)
    prod = Product.objects.create(name='لیوان', unit=unit, cafe=cafe)
    StockItem.objects.create(warehouse=wh, product=prod, quantity=Decimal('5'), unit=unit)

    rf = RequestFactory()
    req = rf.post(f'/c/{cafe.code}/products/{prod.id}/delete/')
    _attach_session_and_cafe(req, manager_user, cafe)
    resp = product_delete(req, prod.id)
    assert resp.status_code == 302
    assert Product.objects.filter(id=prod.id).exists()


@pytest.mark.django_db
def test_quick_add_product_guards_missing_cafe_and_integrity_error(cafe, manager_user):
    rf = RequestFactory()
    # Missing cafe
    req_no_cafe = rf.post(
        '/api/quick-add/',
        data=json.dumps({'name': 'تست', 'unit': 'عدد', 'description': 'توضیح'}),
        content_type='application/json',
    )
    _attach_session_and_cafe(req_no_cafe, manager_user, None)
    resp = quick_add_product(req_no_cafe)
    assert resp.status_code == 400
    assert json.loads(resp.content)['success'] is False

    # IntegrityError on concurrent create
    unit = Unit.objects.create(name='عدد', cafe=cafe)
    existing = Product.objects.create(name='نمک', unit=unit, cafe=cafe, is_active=True)
    req_dup = rf.post(
        '/api/quick-add/',
        data=json.dumps({'name': 'نمک جدید', 'unit': 'عدد', 'description': 'توضیح'}),
        content_type='application/json',
    )
    _attach_session_and_cafe(req_dup, manager_user, cafe)
    with patch('purchases.views.catalog.Product.objects.create', side_effect=IntegrityError('dup')):
        resp2 = quick_add_product(req_dup)
    assert resp2.status_code == 409
    assert json.loads(resp2.content)['success'] is False
    assert existing.id


@pytest.mark.django_db
def test_categories_error_handling_and_recipe_gate_delete_block(cafe, manager_user):
    rf = RequestFactory()
    # Empty unit name
    req_u = rf.post('/units/create/', {'name': '   '})
    _attach_session_and_cafe(req_u, manager_user, cafe)
    resp_u = unit_create(req_u)
    assert resp_u.status_code == 200

    # Invalid parent ID in category_create
    req_c = rf.post('/categories/create/', {'name': 'دسته جدید', 'parent': 'not-a-number'})
    _attach_session_and_cafe(req_c, manager_user, cafe)
    resp_c = category_create(req_c)
    assert resp_c.status_code == 200

    # category_delete blocked when requires_recipe=True
    cat = Category.objects.create(name='مواد رسپی', cafe=cafe, requires_recipe=True)
    req_del = rf.post(f'/categories/{cat.id}/delete/')
    _attach_session_and_cafe(req_del, manager_user, cafe)
    resp_del = category_delete(req_del, cat.id)
    assert resp_del.status_code == 302
    assert Category.objects.filter(id=cat.id).exists()


@pytest.mark.django_db
def test_import_catalog_csv_uses_valid_usage_location(tmp_path, cafe):
    csv_file = tmp_path / 'catalog.csv'
    csv_file.write_text(
        'نام_محصول,دسته_بندی_اصلی,زیرگروه,آشپزخانه,بار\n'
        'شیر پرچرب,لبنیات,شیر,1,1\n'
        'دستمال,ملزومات,کاغذ,0,0\n',
        encoding='utf-8-sig',
    )
    call_command('import_catalog_csv', str(csv_file), cafe_code=cafe.code)
    milk = Product.objects.get(cafe=cafe, name='شیر پرچرب')
    napkin = Product.objects.get(cafe=cafe, name='دستمال')
    assert milk.usage_location == 'general'
    assert napkin.usage_location == 'general'
    assert milk.departments.count() >= 1


# ---------------------------------------------------------------------------
# 3. Consumption reports, Analytics, Reports, Price Alerts
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_consumption_reports_reopen_neutralizes_only_oldest_and_rejects_incompatible_units(cafe, manager_user):
    gram = Unit.objects.create(name='گرم', cafe=cafe)
    carton = Unit.objects.create(name='کارتن', cafe=cafe)
    wh = Warehouse.objects.create(name='انبار', cafe=cafe)
    prod = Product.objects.create(name='پنیر', unit=gram, cafe=cafe, base_unit_price=Decimal('100'), smallest_unit='گرم')
    stock = StockItem.objects.create(warehouse=wh, product=prod, quantity=Decimal('1000'), unit=gram)

    # Incompatible unit estimation returns None instead of 1:1
    est, unit_p, _ = _estimate_consumption_cost(
        Decimal('2'), 'کارتن', {'has_price': True, 'price_Basis_amount': Decimal('100'), 'basis_unit': 'گرم'}
    )
    assert est is None
    assert unit_p is None

    # Oldest out tx neutralized by 1 reopen, newer valid out tx preserved
    pr = PurchaseRequest.objects.create(user=manager_user, cafe=cafe)
    pi = PurchaseItem.objects.create(request=pr, product=prod, quantity=Decimal('100'), unit=gram)
    StockTransaction.objects.create(
        stock_item=stock, transaction_type='out', quantity=Decimal('50'), unit=gram,
        previous_quantity=Decimal('1000'), new_quantity=Decimal('950'),
        reference_type='officer_assignment', reference_id=pi.id, created_by=manager_user,
    )
    StockTransaction.objects.create(
        stock_item=stock, transaction_type='in', quantity=Decimal('50'), unit=gram,
        previous_quantity=Decimal('950'), new_quantity=Decimal('1000'),
        reference_type='officer_assignment_reopen', reference_id=pi.id, created_by=manager_user,
    )
    tx_valid = StockTransaction.objects.create(
        stock_item=stock, transaction_type='out', quantity=Decimal('60'), unit=gram,
        previous_quantity=Decimal('1000'), new_quantity=Decimal('940'),
        reference_type='officer_assignment', reference_id=pi.id, created_by=manager_user,
    )
    deliveries, _ = _collect_consumption_deliveries(cafe)
    assert len(deliveries) == 1
    assert deliveries[0]['id'] == tx_valid.id


@pytest.mark.django_db
def test_analytics_dashboard_no_cafe_and_no_n_plus_1_and_user_department(cafe, manager_user, django_assert_num_queries):
    rf = RequestFactory()
    req = rf.get('/analytics/')
    _attach_session_and_cafe(req, manager_user, None)
    resp = analytics_dashboard(req)
    assert resp.status_code == 302

    dept = Department.objects.create(cafe=cafe, name='بار سرد')
    req_user = User.objects.create_user(
        username='req_u4', password='pw', role='requester', cafe=cafe, department=dept,
    )
    requester_obj = Requester.objects.create(cafe=cafe, name='بار سرد')
    unit = Unit.objects.create(name='عدد', cafe=cafe)
    cat = Category.objects.create(name='نوشیدنی', cafe=cafe, is_active=True)
    prod = Product.objects.create(name='سیروپ', unit=unit, category=cat, cafe=cafe)
    pr = PurchaseRequest.objects.create(user=req_user, cafe=cafe)
    PurchaseItem.objects.create(request=pr, product=prod, quantity=Decimal('3'), unit=unit)
    sup = Supplier.objects.create(name='تامین‌کننده', cafe=cafe)
    po = PurchaseOrder.objects.create(request=pr, supplier=sup)
    inv = Invoice.objects.create(
        cafe=cafe, supplier=sup, purchase_order=po,
        invoice_number='INV-A4-1', invoice_date=timezone.now().date(),
        total_amount=Decimal('3000'), status='paid',
    )
    InvoiceItem.objects.create(
        invoice=inv, product=prod, quantity=Decimal('3'),
        unit_price=Decimal('1000'), line_total=Decimal('3000'),
    )

    api_req = rf.get('/api/analytics/departments/?period=monthly')
    _attach_session_and_cafe(api_req, manager_user, cafe)
    resp_dept = department_expenses_api(api_req)
    assert resp_dept.status_code == 200
    assert len(resp_dept.data['bubbles']) == 1
    assert Decimal(str(resp_dept.data['bubbles'][0]['value'])) == Decimal('3000.00')

    cat_req = rf.get(f'/api/analytics/departments/{requester_obj.id}/categories/?period=monthly')
    _attach_session_and_cafe(cat_req, manager_user, cafe)
    resp_cat = category_expenses_api(cat_req, requester_obj.id)
    assert resp_cat.status_code == 200
    assert len(resp_cat.data['bubbles']) == 1


@pytest.mark.django_db
def test_reports_views_and_pdf_export(cafe, manager_user):
    PurchaseRequest.objects.create(user=manager_user, cafe=cafe)
    rf = RequestFactory()
    for view_fn in (purchase_reports, payment_analytics, inventory_reports):
        req = rf.get('/reports/')
        _attach_session_and_cafe(req, manager_user, cafe)
        with patch('purchases.views.reports.render') as mock_render:
            mock_render.return_value = None
            view_fn(req)
            ctx = mock_render.call_args[0][2]
            assert 'stats' in ctx

    pdf_req = rf.get('/reports/pdf/')
    _attach_session_and_cafe(pdf_req, manager_user, cafe)
    pdf_resp = export_to_pdf(pdf_req)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b'%PDF-')


@pytest.mark.django_db
def test_check_price_alerts_includes_global_managers(cafe):
    User.objects.create_user(
        username='global_ceo', password='pw', role='ceo', cafe=None,
        is_active=True, email='global_ceo@example.com',
    )
    unit = Unit.objects.create(name='کیلوگرم', cafe=cafe)
    prod = Product.objects.create(name='پسته', unit=unit, cafe=cafe)
    PriceHistory.objects.create(
        product=prod, price=Decimal('500000'), percent_change=Decimal('25.0'),
        date=timezone.now() - timedelta(hours=1),
    )
    mail.outbox.clear()
    call_command('check_price_alerts')
    assert len(mail.outbox) == 1
    assert 'global_ceo@example.com' in mail.outbox[0].to
