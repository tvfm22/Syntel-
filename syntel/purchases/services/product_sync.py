# -*- coding: utf-8 -*-
"""Keep the inventory product (`Product`) and the COGS raw material
(`KukerItem` with ``kind='raw'``) of the same cafe in sync.

One product == one raw material. The shared numbers are:

* ``Product.base_unit_price``  == ``KukerItem.price``   (price per smallest unit)
* ``Product.smallest_unit``    == ``KukerItem.out_unit``
* ``Product.name``             == ``KukerItem.name``

Both directions funnel through :func:`sync_raw_material` and
:func:`sync_product`, and a re-entrancy guard stops them bouncing off each
other. Prices are always normalized first (kg → گرم, litre → میل), so a price
typed as "۲۵۰٬۰۰۰ تومان برای هر کیلو" is stored as 250 per gram everywhere.
"""
import threading
from decimal import Decimal, InvalidOperation

from .menu_cogs import normalize_name
from .units import normalize_to_smallest_unit, smallest_unit_of

_GUARD = threading.local()


def _to_decimal(value):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value).strip().replace(',', '').replace('،', ''))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _enter():
    """True when this thread is already inside a sync (skip nested calls)."""
    if getattr(_GUARD, 'active', False):
        return False
    _GUARD.active = True
    return True


def _leave():
    _GUARD.active = False


def unique_item_key(cafe, name, exclude_pk=None):
    """Normalized key that is unique inside one cafe."""
    from ..models import KukerItem
    base = normalize_name(name) or 'item'
    qs = KukerItem.objects.filter(cafe=cafe)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    key = base
    i = 2
    while qs.filter(key=key).exists():
        key = f'{base}#{i}'
        i += 1
    return key


def find_raw_item(cafe, name, product=None):
    """Existing raw material for a product/name inside ``cafe``."""
    from ..models import KukerItem
    if product is not None:
        item = KukerItem.objects.filter(cafe=cafe, product=product).first()
        if item is not None:
            return item
    if not name:
        return None
    item = KukerItem.objects.filter(cafe=cafe, name=name, kind=KukerItem.KIND_RAW).first()
    if item is not None:
        return item
    return KukerItem.objects.filter(cafe=cafe, key=normalize_name(name),
                                    kind=KukerItem.KIND_RAW).first()


def sync_raw_material(item, price=None, unit=None, save=True):
    """Create/update the `Product` that mirrors a raw COGS material.

    ``price`` is the price *as entered* in ``unit`` (e.g. 250000 per کیلوگرم).
    Returns ``(item, product)``.
    """
    from ..models import Product

    if item.kind != item.KIND_RAW:
        return item, None
    if not _enter():
        return item, getattr(item, 'product', None)
    try:
        if price is not None:
            normalized, smallest = normalize_to_smallest_unit(price, unit or item.out_unit)
            item.price = normalized
            item.out_unit = smallest or item.out_unit
        product = item.product
        if product is None:
            from ..models import KukerRecipeLine
            in_recipe = KukerRecipeLine.objects.filter(material=item).exists()
            product, _created = Product.objects.get_or_create(
                cafe=item.cafe, name=item.name,
                defaults={'is_active': in_recipe},
            )
            item.product = product
        product.base_unit_price = item.price
        product.smallest_unit = item.out_unit or ''
        product.save(update_fields=['base_unit_price', 'smallest_unit', 'updated_at'])
        if save:
            item.save(update_fields=['price', 'out_unit', 'product'])
        return item, product
    finally:
        _leave()


def sync_product(product, price=None, unit=None, save=True):
    """Create/update the raw COGS material that mirrors an inventory product.

    ``price`` is the price *as entered* in ``unit``; when omitted the product's
    already-normalized ``base_unit_price`` is used.
    """
    from ..models import KukerItem

    if not _enter():
        return None
    try:
        entered = price
        if entered is not None:
            normalized, smallest = normalize_to_smallest_unit(entered, unit or product.smallest_unit)
            product.base_unit_price = normalized
            product.smallest_unit = smallest or product.smallest_unit
            product.save(update_fields=['base_unit_price', 'smallest_unit', 'updated_at'])

        if product.base_unit_price is None:
            return None

        item = KukerItem.objects.filter(cafe=product.cafe, product=product).first()
        if item is None:
            item = KukerItem.objects.filter(
                cafe=product.cafe, name=product.name, kind=KukerItem.KIND_RAW).first()
        if item is None:
            item = KukerItem(cafe=product.cafe, kind=KukerItem.KIND_RAW,
                             name=product.name, is_custom=True)
            item.key = unique_item_key(product.cafe, product.name)
        else:
            if item.name != product.name:
                new_key = unique_item_key(product.cafe, product.name, exclude_pk=item.pk)
                item.name = product.name
                item.key = new_key
        item.product = product
        item.price = product.base_unit_price
        item.out_unit = product.smallest_unit or smallest_unit_of(item.out_unit)
        if save:
            item.save()
        return item
    finally:
        _leave()


def link_existing_raw_materials(cafe):
    """Backfill: attach every priced raw material of ``cafe`` to a product.

    Used by the data migration and safe to re-run.
    """
    from ..models import KukerItem
    linked = 0
    for item in KukerItem.objects.filter(cafe=cafe, kind=KukerItem.KIND_RAW).order_by('id'):
        if item.product_id:
            continue
        sync_raw_material(item, save=True)
        linked += 1
    return linked
