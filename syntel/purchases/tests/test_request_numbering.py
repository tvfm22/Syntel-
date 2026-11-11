from django.template import Context, Template
from django.test import SimpleTestCase

from purchases.models import PurchaseRequest, WarehouseRequest
from purchases.numbering import REQUEST_NUMBER_START, request_number
from purchases.templatetags.custom_filters import request_number_filter


class RequestNumberHelperTests(SimpleTestCase):
    def test_numbering_starts_at_100000(self):
        self.assertEqual(REQUEST_NUMBER_START, 100000)
        self.assertEqual(request_number(1), '100000')
        self.assertEqual(request_number(2), '100001')
        self.assertEqual(request_number(999), '100998')

    def test_number_is_plain_latin_digits_without_hashtag(self):
        number = request_number(7)
        self.assertIsInstance(number, str)
        self.assertNotIn('#', number)
        self.assertNotIn(',', number)
        self.assertTrue(number.isascii())
        self.assertTrue(number.isdigit())

    def test_empty_or_invalid_values(self):
        for value in (None, '', 'not-a-number'):
            self.assertEqual(request_number(value), '')

    def test_accepts_model_instance(self):
        self.assertEqual(request_number(PurchaseRequest(pk=1)), '100000')


class RequestNumberPropertyTests(SimpleTestCase):
    def test_purchase_request_property(self):
        self.assertEqual(PurchaseRequest(pk=1).request_number, '100000')
        self.assertEqual(PurchaseRequest(pk=12).request_number, '100011')

    def test_warehouse_request_property(self):
        self.assertEqual(WarehouseRequest(pk=1).request_number, '100000')
        self.assertEqual(WarehouseRequest(pk=25).request_number, '100024')

    def test_unsaved_request_has_no_number(self):
        self.assertEqual(PurchaseRequest().request_number, '')


class RequestNumberTemplateTests(SimpleTestCase):
    def test_filter_turns_raw_id_into_display_number(self):
        self.assertEqual(request_number_filter(1), '100000')
        tpl = Template('{% load custom_filters %}{{ ref|request_number }}')
        self.assertEqual(tpl.render(Context({'ref': 5})), '100004')

    def test_model_property_renders_without_hashtag(self):
        tpl = Template('{{ req.request_number }}')
        self.assertEqual(tpl.render(Context({'req': PurchaseRequest(pk=3)})), '100002')
