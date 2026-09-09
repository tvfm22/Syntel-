"""سنگ‌آزمایش: فیلتر تاریخِ داشبورد مسئول انبار (ناوبری روزِ ثبت درخواست).

گزارش کاربر (2026-09-09): فیلتر تاریخ از کار افتاده بود و صفحه عوض نمی‌شد —
انتخابگر تاریخ به ?day= می‌رفت ولی منطقِ صفحه هنوز ?delivery= (پارامترِ قدیمیِ
روزِ تحویل) را می‌خواند و به امروز برمی‌گشت. این تست جریانِ انتخاب تاریخ را
سرتاسر چک می‌کند: ریدایرکتِ URL بدون پارامتر، تغییر واقعی محتوای صفحه با عوض
شدن روز، و هم‌خوانی پارامترِ فرم با منطقِ view.
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from purchases.models import (
    Cafe, Product, PurchaseItem, PurchaseRequest, StockItem, Unit, Warehouse,
)

User = get_user_model()


class OfficerDashboardDayNavTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.officer = User.objects.create_user(
            username='wo_daynav', password='testpass123', role='warehouse')
        self.manager = User.objects.create_user(
            username='mgr_daynav', password='testpass123', role='manager')
        self.unit = Unit.objects.create(name='Kilogram', symbol='kg')
        self.cafe = Cafe.objects.create(name='Main', code='main', is_active=True)
        session = self.client.session
        session['selected_cafe_id'] = self.cafe.id
        session['selected_cafe_code'] = self.cafe.code
        session.save()
        self.officer.cafe = self.cafe
        self.officer.save()
        self.manager.cafe = self.cafe
        self.manager.save()
        self.client.force_login(self.officer)
        self.warehouse = Warehouse.objects.create(name='WH', cafe=self.cafe)
        self.url = reverse('warehouse_officer_dashboard', args=['main'])

    def _mk_request(self, product, on_date):
        """درخواستِ مدیرعامل با قلمی که در انبار موجودی دارد — ثبت در روزِ on_date"""
        req = PurchaseRequest.objects.create(
            user=self.manager, cafe=self.cafe, status='pending',
            delivery_date=on_date + timedelta(days=2))  # روزِ تحویل ≠ روزِ ثبت
        # created_at خودکار است (auto_now_add) — تاریخِ روزِ ثبت را با update مستقیم می‌گذاریم
        PurchaseRequest.objects.filter(pk=req.pk).update(created_at=timezone.make_aware(
            datetime.combine(on_date, datetime.min.time()) + timedelta(hours=10)))
        PurchaseItem.objects.create(request=req, product=product,
                                    name=product.name, quantity=3, unit='kg')
        return req

    def _stocked(self, name):
        unit2 = Unit.objects.create(name=name, symbol='u')
        prod = Product.objects.create(name=name, unit=unit2, cafe=self.cafe)
        StockItem.objects.create(product=prod, warehouse=self.warehouse,
                                 quantity=50, unit=unit2)
        return prod

    def test_bare_url_redirects_to_today(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        today = timezone.localtime(timezone.now()).date().isoformat()
        self.assertIn(f'day={today}', resp.url)

    def test_changing_day_actually_changes_page_content(self):
        """گزارشِ کاربر: «فیلتر تاریخ از کار افتاده و صفحه عوض نمی‌شود»"""
        today = timezone.localtime(timezone.now()).date()
        yesterday = today - timedelta(days=1)
        p_today = self._stocked('today prod')
        p_yest = self._stocked('yest prod')
        req_today = self._mk_request(p_today, today)
        req_yest = self._mk_request(p_yest, yesterday)

        page_today = self.client.get(self.url, {'day': today.isoformat()})
        self.assertEqual(page_today.status_code, 200)
        ids = [c['request'].id for c in page_today.context['request_cards']]
        self.assertIn(req_today.id, ids)
        self.assertNotIn(req_yest.id, ids)

        page_yest = self.client.get(self.url, {'day': yesterday.isoformat()})
        self.assertEqual(page_yest.status_code, 200)
        ids2 = [c['request'].id for c in page_yest.context['request_cards']]
        self.assertIn(req_yest.id, ids2)
        self.assertNotIn(req_today.id, ids2)

    def test_picker_param_matches_view(self):
        """روی صفحه، انتخابگر/فلش‌ها باید به همان پارامتری بروند که view می‌خواند"""
        resp = self.client.get(self.url, {'day': timezone.localtime(timezone.now()).date().isoformat()})
        self.assertContains(resp, '?day=')
        self.assertNotContains(resp, "?delivery=")
        # فلش «روز بعد» هم با day ساخته می‌شود
        self.assertContains(resp, 'onchange="var u=\'?day=\'+this.value')

    def test_old_delivery_param_no_longer_bounces(self):
        """پارامتر قدیمی ?delivery نباید صفحه را به امروز برگرداند/خطا بدهد"""
        today = timezone.localtime(timezone.now()).date()
        resp = self.client.get(self.url, {'delivery': today.isoformat()})
        self.assertNotEqual(resp.status_code, 500)
        # بدون day صریح → ریدایرکت به روزِ امروز (بدون از دست دادن خواستهٔ کاربر)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('day=', resp.url)
