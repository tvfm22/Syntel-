"""سنگ‌آزمایش: کارت درخواستی که همه اقلامش ثبت شده به پایین لیست داشبورد مسئول انبار می‌رود."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Unit, Product, PurchaseRequest, PurchaseItem,
    OfficerAssignment, Warehouse, StockItem,
)

User = get_user_model()


class OfficerDashboardCardSortTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.officer = User.objects.create_user(
            username='wo_sort_officer', password='testpass123', role='warehouse')

        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()
        self.officer.cafe = self.cafe
        self.officer.save()

        self.ceo = User.objects.create_user(
            username='ceo_sorter', password='testpass123', role='manager', cafe=self.cafe)
        self.warehouse = Warehouse.objects.create(name='WH1', cafe=self.cafe)
        self.prod_old = Product.objects.create(name='old prod', unit=self.unit)
        self.prod_new = Product.objects.create(name='new prod', unit=self.unit)
        StockItem.objects.create(product=self.prod_old, warehouse=self.warehouse,
                                 quantity=Decimal('50'), unit=self.unit)
        StockItem.objects.create(product=self.prod_new, warehouse=self.warehouse,
                                 quantity=Decimal('50'), unit=self.unit)

    def test_fully_processed_request_moves_to_bottom(self):
        """دو درخواستِ امروز: قدیمی‌تر تامین‌شده → کارتش پایین‌تر از درخواست در انتظار"""
        now = timezone.localtime(timezone.now())
        req_old = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            delivery_date=now.date(), created_at=now - timedelta(hours=2))
        PurchaseItem.objects.create(request=req_old, product=self.prod_old,
                                    name='old prod', quantity=Decimal('5'), unit='kg')
        req_new = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            delivery_date=now.date(), created_at=now - timedelta(minutes=5))
        PurchaseItem.objects.create(request=req_new, product=self.prod_new,
                                    name='new prod', quantity=Decimal('3'), unit='kg')

        # قلم درخواست قدیمی تامین شده → کارت آن باید پایین برود
        OfficerAssignment.objects.create(
            item=PurchaseItem.objects.get(request=req_old),
            target_role='warehouse', status='fulfilled',
            assigned_by=self.officer, assigned_to=self.officer,
            supplied_quantity=Decimal('5'))

        self.client.force_login(self.officer)
        # GET بدون day به ‎?day=<امروز> ریدایرکت می‌شود؛ با follow دنبال می‌کنیم
        resp = self.client.get(reverse('warehouse_officer_dashboard', args=[self.cafe.code]), follow=True)
        self.assertEqual(resp.status_code, 200)
        # ترتیب کارت‌ها از context می‌خوانیم (جستجوی رشته‌ای در HTML به رنگ‌های hex مثل #123 گیر می‌دهد)
        ids = [card['request'].id for card in resp.context['request_cards']]
        self.assertIn(req_old.id, ids)
        self.assertIn(req_new.id, ids)
        self.assertLess(ids.index(req_new.id), ids.index(req_old.id),
                        'درخواستِ پردازش‌شده باید پایین‌تر از درخواستِ در انتظار باشد')

    def test_requests_appear_on_registration_day_not_delivery_day(self):
        """هر درخواست در صفحهٔ روزِ «ثبت» خودش می‌آید — زمان تحویلِ درخواست‌شده
        تعیین‌کنندهٔ روزِ صفحه نیست و فقط روی کارت نمایش داده می‌شود"""
        now = timezone.localtime(timezone.now())
        yest = now.date() - timedelta(days=1)
        # نقطهای قطعاً در «امروز» و قطعاً قبل از الان: کمکردن یک بازهٔ ثابت
        # (مثلاً ۳ ساعت) از الان، حوالی نیمهشب به دیروز میافتاد و تست قرمز
        # میشد، بدون اینکه ایرادی در برنامه باشد.
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        earlier_today = today_midnight + (now - today_midnight) / 2

        # ثبت‌شدهٔ دیروز با تحویلِ درخواستیِ دور → باید روی صفحهٔ دیروز باشد نه امروز
        yesterday = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending',
            delivery_date=now.date() + timedelta(days=5))
        PurchaseRequest.objects.filter(pk=yesterday.pk).update(
            created_at=timezone.make_aware(
                datetime.combine(yest, datetime.min.time())) + timedelta(hours=10))

        # ثبت‌شدهٔ امروز با «تحویل فوری» (بدون تاریخ) → باید روی صفحهٔ امروز باشد
        today_immediate = PurchaseRequest.objects.create(
            user=self.ceo, cafe=self.cafe, status='pending', delivery_immediate=True)
        PurchaseRequest.objects.filter(pk=today_immediate.pk).update(
            created_at=earlier_today)

        for req, prod in ((yesterday, self.prod_new), (today_immediate, self.prod_new)):
            PurchaseItem.objects.create(request=req, product=prod,
                                        name=prod.name, quantity=Decimal('3'), unit='kg')

        self.client.force_login(self.officer)
        # صفحهٔ امروز (پیش‌فرض): فقط ثبت‌شده‌های امروز — «فوری» هم اینجا دیده می‌شود
        resp = self.client.get(reverse('warehouse_officer_dashboard',
                                       args=[self.cafe.code]), follow=True)
        ids = [card['request'].id for card in resp.context['request_cards']]
        self.assertIn(today_immediate.id, ids, 'درخواستِ ثبت‌شدهٔ امروز باید روی صفحهٔ امروز باشد')
        self.assertNotIn(yesterday.id, ids, 'درخواستِ ثبت‌شدهٔ دیروز نباید روی صفحهٔ امروز بیاید')
        # زمان تحویلِ درخواست‌شده روی کارت هست — برچسب «تحویل فوری» باید دیده شود
        self.assertIn('تحویل فوری', resp.content.decode('utf-8'))
        # صفحهٔ دیروز: فقط ثبت‌شدهٔ دیروز — با هر تحویلِ درخواستی که داشته باشد
        resp2 = self.client.get(reverse('warehouse_officer_dashboard', args=[self.cafe.code])
                                + f'?day={yest.isoformat()}')
        ids2 = [card['request'].id for card in resp2.context['request_cards']]
        self.assertIn(yesterday.id, ids2, 'درخواستِ ثبت‌شدهٔ دیروز باید روی صفحهٔ دیروز باشد')
        self.assertNotIn(today_immediate.id, ids2,
                         'درخواستِ ثبت‌شدهٔ امروز نباید روی صفحهٔ دیروز بیاید')
