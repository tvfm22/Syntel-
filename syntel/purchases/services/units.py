# -*- coding: utf-8 -*-
"""Smallest-unit price normalization.

Every price in the COGS / product layer is stored per *smallest* unit so that
recipe quantities (grams, millilitres, pieces) multiply straight into a cost:

    * کیلوگرم / kg / کیلو  -> price / 1000, stored per «گرم»
    * لیتر / L             -> price / 1000, stored per «میل»
    * گرم / میل / عدد / پرس / بسته ... -> stored as-is, per 1 unit

Anything the caller does not recognise is passed through untouched, so an
unknown unit never silently loses a factor of 1000.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Spelling variants (whitespace / ZWNJ stripped before matching)
_KG_VARIANTS = {'کیلوگرم', 'کیلوگرام', 'کیلو', 'kg', 'kgs', 'kilogram'}
_LITER_VARIANTS = {'لیتر', 'لیتری', 'l', 'lt', 'ltr', 'liter', 'litre'}
_GRAM_VARIANTS = {'گرم', 'گرمی', 'g', 'gr', 'gram'}
_MIL_VARIANTS = {'میل', 'میلی لیتر', 'میلیلیتر', 'سی سی', 'ml', 'cc', 'milliliter'}

#: canonical label written into the DB for each family
SMALLEST_UNIT_KG = 'گرم'
SMALLEST_UNIT_LITER = 'میل'

_THOUSAND = Decimal('1000')
_DIGIT_TRANS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def _squash(value):
    """Lowercase + strip spaces / ZWNJ / Arabic yeh-kaf variants for matching."""
    if value is None:
        return ''
    s = str(value).strip().lower()
    for a, b in (('ي', 'ی'), ('ك', 'ک'), ('‌', ''), (' ', ''), ('_', '')):
        s = s.replace(a, b)
    return s


_KG_SQUASHED = frozenset(_squash(u) for u in _KG_VARIANTS)
_LITER_SQUASHED = frozenset(_squash(u) for u in _LITER_VARIANTS)
_GRAM_SQUASHED = frozenset(_squash(u) for u in _GRAM_VARIANTS)
_MIL_SQUASHED = frozenset(_squash(u) for u in _MIL_VARIANTS)


def _canonical_family(unit_name):
    """Return ('kg'|'liter'|'gram'|'mil'|None) for a unit name."""
    s = _squash(unit_name)
    if not s:
        return None
    if s in _KG_SQUASHED:
        return 'kg'
    if s in _LITER_SQUASHED:
        return 'liter'
    if s in _GRAM_SQUASHED:
        return 'gram'
    if s in _MIL_SQUASHED:
        return 'mil'
    return None


def _to_decimal(value):
    if value is None or value == '':
        return None
    try:
        cleaned = (
            str(value)
            .translate(_DIGIT_TRANS)
            .strip()
            .replace(',', '')
            .replace('،', '')
            .replace('٬', '')
            .replace('٫', '.')
        )
        dec = Decimal(cleaned)
        if not dec.is_finite():
            return None
        return dec
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_to_smallest_unit(price, unit_name):
    """Normalize ``price`` (per ``unit_name``) to a price per 1 smallest unit.

    Returns ``(price_per_smallest_unit, smallest_unit_name)``.

    >>> normalize_to_smallest_unit(250000, 'کیلوگرم')
    (Decimal('250.0000'), 'گرم')
    >>> normalize_to_smallest_unit(90000, 'لیتر')
    (Decimal('90.0000'), 'میل')
    >>> normalize_to_smallest_unit(4200, 'گرم')
    (Decimal('4200'), 'گرم')
    >>> normalize_to_smallest_unit(15000, 'پرس')
    (Decimal('15000'), 'پرس')
    """
    unit = (str(unit_name).strip() if unit_name else '')
    amount = _to_decimal(price)
    family = _canonical_family(unit)

    if family == 'kg':
        if amount is None:
            return None, SMALLEST_UNIT_KG
        return amount / _THOUSAND, SMALLEST_UNIT_KG
    if family == 'liter':
        if amount is None:
            return None, SMALLEST_UNIT_LITER
        return amount / _THOUSAND, SMALLEST_UNIT_LITER
    if family == 'gram':
        return amount, SMALLEST_UNIT_KG
    if family == 'mil':
        return amount, SMALLEST_UNIT_LITER
    # already a smallest unit (عدد، پرس، بسته، اسلایس، ...) or unknown → unchanged
    return amount, unit


def smallest_unit_of(unit_name):
    """The unit a price entered in ``unit_name`` is stored against."""
    _, smallest = normalize_to_smallest_unit(None, unit_name)
    return smallest


def convert_quantity_to_smallest_unit(quantity, unit_name):
    """Convert a recipe quantity to the matching smallest unit.

    >>> convert_quantity_to_smallest_unit(Decimal('0.3'), 'کیلوگرم')
    (Decimal('300.0'), 'گرم')
    """
    unit = (str(unit_name).strip() if unit_name else '')
    amount = _to_decimal(quantity)
    family = _canonical_family(unit)

    if family == 'kg':
        if amount is None:
            return None, SMALLEST_UNIT_KG
        return amount * _THOUSAND, SMALLEST_UNIT_KG
    if family == 'liter':
        if amount is None:
            return None, SMALLEST_UNIT_LITER
        return amount * _THOUSAND, SMALLEST_UNIT_LITER
    if family == 'gram':
        return amount, SMALLEST_UNIT_KG
    if family == 'mil':
        return amount, SMALLEST_UNIT_LITER
    return amount, unit


def is_bigger_than_smallest(unit_name):
    """True when a price entered in this unit needs dividing (kg / litre)."""
    return _canonical_family(unit_name) in ('kg', 'liter')


def unit_family(unit_name):
    """Public view of the unit family: 'kg' | 'liter' | 'gram' | 'mil' | None.

    ``None`` means "not one of the four measured families" — عدد, پرس, بسته
    and anything the user invents all fall here and are treated as countable.
    """
    return _canonical_family(unit_name)


def units_are_compatible(first, second):
    """True when two unit names measure the same thing.

    - Mass units (kg, gram) are compatible with each other.
    - Volume units (liter, mil) are compatible with each other.
    - Countable/unknown units require an exact squashed match (so 'عدد' vs 'کارتن' is False).
    """
    fam_a, fam_b = _canonical_family(first), _canonical_family(second)
    if fam_a in ('kg', 'gram') and fam_b in ('kg', 'gram'):
        return True
    if fam_a in ('liter', 'mil') and fam_b in ('liter', 'mil'):
        return True
    if fam_a is None and fam_b is None:
        return _squash(first) == _squash(second)
    return False


def normalize_package_price(amount, package_size, package_unit):
    """Price of one package → price per 1 smallest unit.

    A «بستهٔ ۵ کیلوگرمی» costing 1,750,000 becomes 350 per gram:

    >>> normalize_package_price(1750000, 5, 'کیلوگرم')
    (Decimal('350.0000'), 'گرم')
    """
    quantity, smallest = convert_quantity_to_smallest_unit(package_size, package_unit)
    price = _to_decimal(amount)
    if price is None:
        return None, smallest
    if quantity is None or quantity <= 0:
        return None, smallest
    return (price / quantity).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP), smallest
