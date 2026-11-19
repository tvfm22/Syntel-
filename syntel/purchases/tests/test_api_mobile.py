import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from purchases.models import Cafe, Category, Product, Unit, Warehouse

User = get_user_model()


@pytest.mark.django_db
class TestMobileApiSmoke:
    def setup_method(self):
        self.client = APIClient()
        self.cafe = Cafe.objects.create(name="کافه تست", code="testcafe", is_active=True)
        self.unit = Unit.objects.create(name="کیلوگرم", cafe=self.cafe)
        self.category = Category.objects.create(name="عمومی", cafe=self.cafe)
        self.warehouse = Warehouse.objects.create(name="انبار مرکزی", cafe=self.cafe, is_active=True)
        self.product = Product.objects.create(
            name="قهوه",
            category=self.category,
            unit=self.unit,
            cafe=self.cafe,
            is_active=True,
        )
        self.requester = User.objects.create_user(
            username="req_mobile",
            password="pass1234",
            role="kitchen",
            cafe=self.cafe,
        )
        self.warehouse_user = User.objects.create_user(
            username="wh_mobile",
            password="pass1234",
            role="warehouse",
            cafe=self.cafe,
        )
        self.purchasing_user = User.objects.create_user(
            username="buy_mobile",
            password="pass1234",
            role="purchasing",
            cafe=self.cafe,
        )

    def _login(self, username, password="pass1234"):
        resp = self.client.post(
            "/api/mobile/auth/login/",
            {"username": username, "password": password},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert "token" in data
        assert data["user"]["cafe_code"] == self.cafe.code
        return data["token"]

    def test_auth_login_me_logout(self):
        token = self._login("req_mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        me_resp = self.client.get("/api/mobile/auth/me/")
        assert me_resp.status_code == 200
        assert me_resp.json()["user"]["username"] == "req_mobile"

        logout_resp = self.client.post("/api/mobile/auth/logout/")
        assert logout_resp.status_code == 200

        me_after = self.client.get("/api/mobile/auth/me/")
        assert me_after.status_code == 401

    def test_requester_endpoints(self):
        token = self._login("req_mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        for url in [
            f"/{self.cafe.code}/api/mobile/requester/summary/",
            f"/{self.cafe.code}/api/mobile/requester/products/",
            f"/{self.cafe.code}/api/mobile/requester/requests/",
            f"/{self.cafe.code}/api/mobile/requester/received-items/",
        ]:
            resp = self.client.get(url)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.content}"

    def test_warehouse_endpoints(self):
        token = self._login("wh_mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        for url in [
            f"/{self.cafe.code}/api/mobile/warehouse/dashboard/",
            f"/{self.cafe.code}/api/mobile/warehouse/draft/",
            f"/{self.cafe.code}/api/mobile/warehouse/stock/",
            f"/{self.cafe.code}/api/mobile/warehouse/vouchers/",
        ]:
            resp = self.client.get(url)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.content}"

    def test_purchasing_endpoints(self):
        token = self._login("buy_mobile")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        for url in [
            f"/{self.cafe.code}/api/mobile/purchasing/dashboard/",
            f"/{self.cafe.code}/api/mobile/purchasing/history/",
        ]:
            resp = self.client.get(url)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.content}"
