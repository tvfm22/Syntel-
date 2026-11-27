"""
سرویس‌های ماژول اموال و اثاثیه سکشن‌ها (Assets & Furnishings).
تمام عملیات تغییر موجودی در `transaction.atomic()` و با قفل `select_for_update()` انجام می‌شود.
"""
from __future__ import annotations

from decimal import Decimal
import io
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from PIL import Image, ImageOps, features

from purchases.models import (
    AssetAudit,
    AssetAuditItem,
    AssetCatalog,
    AssetMovement,
    AssetUnit,
    DepartmentAssetStock,
    PurchaseItem,
    PurchaseRequest,
    User,
)
from purchases.views.helpers import (
    user_can_access_department_assets,
    user_can_manage_assets,
)


class AssetServiceError(Exception):
    """خطای پایه سرویس اموال."""


class AssetPermissionError(AssetServiceError):
    """عدم دسترسی کاربر برای انجام عملیات اموال."""


class AssetValidationError(AssetServiceError):
    """خطای اعتبارسنجی داده‌های اموال."""


class InsufficientAssetStockError(AssetValidationError):
    """موجودی سالم یا قابل انتقال اموال کافی نیست."""


class AssetStateError(AssetServiceError):
    """وضعیت فعلی سند یا تجهیز اجازه این عملیات را نمی‌دهد."""


def _require_cafe_access(user, cafe) -> None:
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        raise AssetPermissionError('احراز هویت کاربر فعال الزامی است.')
    if cafe is None:
        raise AssetValidationError('کافه مشخص نشده است.')
    is_global = getattr(user, 'is_superuser', False) or (
        getattr(user, 'cafe_id', None) is None
        and getattr(user, 'role', None) in User.CAFE_SELECTION_ROLES
    )
    if not is_global and getattr(user, 'cafe_id', None) != cafe.id:
        raise AssetPermissionError('شما به این کافه دسترسی ندارید.')


def _require_asset_manager(user, cafe) -> None:
    _require_cafe_access(user, cafe)
    if not user_can_manage_assets(user):
        raise AssetPermissionError('این عملیات فقط برای مدیران یا مسئول انبار مجاز است.')


def _require_department_access(user, cafe, department) -> None:
    _require_cafe_access(user, cafe)
    if department is None or department.cafe_id != cafe.id:
        raise AssetValidationError('بخش انتخاب‌شده متعلق به این کافه نیست.')
    if not user_can_access_department_assets(user, department):
        raise AssetPermissionError('شما به اموال این بخش دسترسی ندارید.')


def _encode_pil_image(img: Image.Image, target_size: tuple[int, int], stem: str, quality: int = 82) -> ContentFile:
    copy_img = img.copy()
    copy_img.thumbnail(target_size, Image.Resampling.LANCZOS)

    use_webp = bool(features.check('webp'))
    buf = io.BytesIO()
    if use_webp:
        if copy_img.mode not in ('RGB', 'RGBA'):
            has_alpha = 'A' in copy_img.mode or copy_img.info.get('transparency') is not None
            copy_img = copy_img.convert('RGBA' if has_alpha else 'RGB')
        copy_img.save(buf, format='WEBP', quality=quality, method=4)
        ext = 'webp'
    else:
        if copy_img.mode in ('RGBA', 'LA') or (copy_img.mode == 'P' and 'transparency' in copy_img.info):
            rgba = copy_img.convert('RGBA')
            bg = Image.new('RGB', rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            copy_img = bg
        elif copy_img.mode != 'RGB':
            copy_img = copy_img.convert('RGB')
        copy_img.save(buf, format='JPEG', quality=quality, optimize=True)
        ext = 'jpg'

    return ContentFile(buf.getvalue(), name=f'{stem}.{ext}')


def _open_oriented_image(uploaded_file) -> tuple[Image.Image, str]:
    if uploaded_file is None:
        raise AssetValidationError('فایل تصویر ارسال نشده است.')
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)
    try:
        with Image.open(uploaded_file) as raw_img:
            transposed = ImageOps.exif_transpose(raw_img)
            transposed.load()
            img = transposed.copy()
    except Exception as exc:
        raise AssetValidationError('فایل ارسالی یک تصویر معتبر نیست.') from exc
    stem = Path(getattr(uploaded_file, 'name', '') or 'asset').stem or 'asset'
    return img, stem


def compress_and_thumbnail_image(
    uploaded_file,
    max_size: tuple[int, int] = (1200, 1200),
    thumb_size: tuple[int, int] = (240, 240),
) -> tuple[ContentFile, ContentFile]:
    """فشرده‌سازی تصویر اصلی (با اصلاح چرخش EXIF) و ساخت تصویر بندانگشتی."""
    img, stem = _open_oriented_image(uploaded_file)
    main_file = _encode_pil_image(img, max_size, stem)
    thumb_file = _encode_pil_image(img, thumb_size, f'{stem}_thumb')
    return main_file, thumb_file


def compress_evidence_image(
    uploaded_file,
    max_size: tuple[int, int] = (1200, 1200),
) -> ContentFile:
    """فشرده‌سازی تصویر مستندات / خرابی / انبارگردانی با اصلاح چرخش EXIF."""
    img, stem = _open_oriented_image(uploaded_file)
    return _encode_pil_image(img, max_size, stem)


def sync_serialized_department_stock(department, asset):
    """همگام‌سازی خودکار DepartmentAssetStock از روی پلاک‌های مستقر در بخش."""
    if department is None or asset is None or asset.tracking_mode != 'serialized':
        return None
    if department.cafe_id != asset.cafe_id:
        raise AssetValidationError('بخش و مال باید متعلق به یک کافه باشند.')

    with transaction.atomic():
        units = list(
            AssetUnit.objects.select_for_update()
            .filter(cafe_id=department.cafe_id, department=department, asset=asset)
        )
        healthy = sum(1 for u in units if u.status == 'active')
        damaged = sum(1 for u in units if u.status in ('needs_repair', 'in_repair'))

        stock, created = DepartmentAssetStock.objects.select_for_update().get_or_create(
            department=department,
            asset=asset,
            defaults={
                'cafe': department.cafe,
                'quantity_healthy': healthy,
                'quantity_damaged': damaged,
            },
        )
        if not created:
            stock.quantity_healthy = healthy
            stock.quantity_damaged = damaged
            stock.save(update_fields=['quantity_healthy', 'quantity_damaged', 'updated_at'])
        return stock


def allocate_initial_or_addition(
    cafe,
    department,
    asset,
    quantity,
    user,
    movement_type='initial',
    unit_value=None,
    min_expected_quantity=None,
    location_note='',
    invoice=None,
    reason='',
) -> AssetMovement:
    """ثبت موجودی اولیه یا ورود جدید برای اقلام تعدادی بخش."""
    _require_asset_manager(user, cafe)
    if movement_type not in ('initial', 'addition'):
        raise AssetValidationError('نوع گردش باید initial یا addition باشد.')
    if department is None or department.cafe_id != cafe.id:
        raise AssetValidationError('بخش انتخاب‌شده متعلق به این کافه نیست.')
    if asset is None or asset.cafe_id != cafe.id:
        raise AssetValidationError('مال انتخاب‌شده متعلق به این کافه نیست.')
    if asset.tracking_mode != 'quantity':
        raise AssetValidationError('برای اقلام پلاکدار باید از ثبت پلاک تکی استفاده شود.')
    if invoice is not None and invoice.cafe_id != cafe.id:
        raise AssetValidationError('فاکتور انتخاب‌شده متعلق به این کافه نیست.')

    qty = int(quantity)
    if qty <= 0:
        raise AssetValidationError('تعداد باید بزرگتر از صفر باشد.')

    snapshot_val = (
        Decimal(str(unit_value))
        if unit_value is not None
        else (asset.estimated_unit_value or Decimal('0'))
    )
    now = timezone.now()

    with transaction.atomic():
        stock, created = DepartmentAssetStock.objects.select_for_update().get_or_create(
            department=department,
            asset=asset,
            defaults={
                'cafe': cafe,
                'quantity_healthy': 0,
                'quantity_damaged': 0,
                'min_expected_quantity': int(min_expected_quantity) if min_expected_quantity is not None else 0,
                'location_note': location_note or '',
            },
        )
        stock.quantity_healthy += qty
        if not created:
            if min_expected_quantity is not None:
                stock.min_expected_quantity = int(min_expected_quantity)
            if location_note:
                stock.location_note = location_note
        stock.save()

        movement = AssetMovement.objects.create(
            cafe=cafe,
            movement_type=movement_type,
            status='approved',
            asset=asset,
            to_department=department,
            quantity=qty,
            condition_bucket='healthy',
            unit_value_snapshot=snapshot_val,
            reason=reason,
            invoice=invoice,
            created_by=user,
            approved_by=user,
            approved_at=now,
        )
    return movement


def create_serialized_unit(
    cafe,
    department,
    asset,
    asset_tag,
    user,
    serial_number='',
    purchase_date=None,
    warranty_until=None,
    purchase_price=0,
    invoice=None,
    custodian=None,
    photo=None,
    notes='',
) -> AssetUnit:
    """ایجاد یک تجهیز پلاک‌دار جدید، ثبت گردش ورود و همگام‌سازی موجودی بخش."""
    _require_asset_manager(user, cafe)
    if asset is None or asset.cafe_id != cafe.id:
        raise AssetValidationError('مال انتخاب‌شده متعلق به این کافه نیست.')
    if asset.tracking_mode != 'serialized':
        raise AssetValidationError('ثبت پلاک فقط برای اقلام با روش ردیابی پلاک‌دار مجاز است.')
    if department is not None and department.cafe_id != cafe.id:
        raise AssetValidationError('بخش انتخاب‌شده متعلق به این کافه نیست.')
    if invoice is not None and invoice.cafe_id != cafe.id:
        raise AssetValidationError('فاکتور انتخاب‌شده متعلق به این کافه نیست.')
    if custodian is not None and custodian.cafe_id not in (None, cafe.id):
        raise AssetValidationError('مسئول تحویل باید متعلق به همین کافه باشد.')

    clean_tag = (asset_tag or '').strip()
    if not clean_tag:
        raise AssetValidationError('کد پلاک الزامی است.')

    price_dec = Decimal(str(purchase_price or 0))
    now = timezone.now()

    with transaction.atomic():
        if AssetUnit.objects.filter(cafe=cafe, asset_tag=clean_tag).exists():
            raise AssetValidationError(f'کد پلاک «{clean_tag}» قبلاً در این کافه ثبت شده است.')
        try:
            unit = AssetUnit.objects.create(
                cafe=cafe,
                asset=asset,
                department=department,
                asset_tag=clean_tag,
                serial_number=(serial_number or '').strip(),
                purchase_date=purchase_date,
                warranty_until=warranty_until,
                purchase_price=price_dec,
                invoice=invoice,
                status='active',
                custodian=custodian,
                photo=photo,
                notes=notes or '',
            )
        except IntegrityError as exc:
            raise AssetValidationError(f'کد پلاک «{clean_tag}» تکراری است.') from exc

        AssetMovement.objects.create(
            cafe=cafe,
            movement_type='addition',
            status='approved',
            asset=asset,
            asset_unit=unit,
            to_department=department,
            quantity=1,
            condition_bucket='healthy',
            unit_value_snapshot=price_dec if price_dec > 0 else (asset.estimated_unit_value or Decimal('0')),
            reason=notes or f'ثبت پلاک {clean_tag}',
            invoice=invoice,
            created_by=user,
            approved_by=user,
            approved_at=now,
        )

        if department is not None:
            sync_serialized_department_stock(department, asset)

    return unit


def initiate_transfer(
    cafe,
    asset,
    from_department,
    to_department,
    quantity,
    user,
    asset_unit=None,
    reason='',
    require_handshake=True,
) -> AssetMovement:
    """شروع انتقال مال بین دو بخش (تأیید دوطرفه یا اعمال مستقیم توسط مدیر)."""
    if from_department is None or to_department is None:
        raise AssetValidationError('بخش مبدأ و مقصد هر دو الزامی هستند.')
    if from_department.cafe_id != cafe.id or to_department.cafe_id != cafe.id:
        raise AssetValidationError('بخش‌های مبدأ و مقصد باید متعلق به این کافه باشند.')
    if from_department.id == to_department.id:
        raise AssetValidationError('بخش مبدأ و مقصد نمی‌توانند یکسان باشند.')
    if asset is None or asset.cafe_id != cafe.id:
        raise AssetValidationError('مال انتخاب‌شده متعلق به این کافه نیست.')

    if require_handshake:
        _require_department_access(user, cafe, from_department)
    else:
        _require_asset_manager(user, cafe)

    now = timezone.now()

    with transaction.atomic():
        if asset.tracking_mode == 'serialized' or asset_unit is not None:
            if asset_unit is None:
                raise AssetValidationError('برای انتقال اقلام پلاکدار انتخاب پلاک الزامی است.')
            unit = AssetUnit.objects.select_for_update().get(pk=asset_unit.pk)
            if unit.cafe_id != cafe.id or unit.asset_id != asset.id:
                raise AssetValidationError('پلاک انتخاب‌شده با کالا یا کافه مطابقت ندارد.')
            if unit.department_id != from_department.id:
                raise AssetValidationError('این پلاک در حال حاضر در بخش مبدأ مستقر نیست.')
            if unit.status in ('lost', 'disposed'):
                raise AssetStateError('پلاک مفقودی یا اسقاط‌شده قابل انتقال نیست.')
            if AssetMovement.objects.filter(
                cafe=cafe,
                movement_type='transfer',
                status='pending_receipt',
                asset_unit=unit,
            ).exists():
                raise AssetStateError('برای این پلاک یک انتقال در انتظار تحویل وجود دارد.')

            condition = 'healthy' if unit.status == 'active' else 'damaged'
            val = unit.purchase_price or asset.estimated_unit_value or Decimal('0')

            if require_handshake:
                return AssetMovement.objects.create(
                    cafe=cafe,
                    movement_type='transfer',
                    status='pending_receipt',
                    asset=asset,
                    asset_unit=unit,
                    from_department=from_department,
                    to_department=to_department,
                    quantity=1,
                    condition_bucket=condition,
                    unit_value_snapshot=val,
                    reason=reason,
                    created_by=user,
                )

            unit.department = to_department
            unit.save(update_fields=['department', 'updated_at'])
            sync_serialized_department_stock(from_department, asset)
            sync_serialized_department_stock(to_department, asset)
            return AssetMovement.objects.create(
                cafe=cafe,
                movement_type='transfer',
                status='approved',
                asset=asset,
                asset_unit=unit,
                from_department=from_department,
                to_department=to_department,
                quantity=1,
                condition_bucket=condition,
                unit_value_snapshot=val,
                reason=reason,
                created_by=user,
                approved_by=user,
                received_by=user,
                approved_at=now,
                received_at=now,
            )

        qty = int(quantity)
        if qty <= 0:
            raise AssetValidationError('تعداد انتقال باید بزرگتر از صفر باشد.')

        src_stock = (
            DepartmentAssetStock.objects.select_for_update()
            .filter(department=from_department, asset=asset)
            .first()
        )
        src_healthy = src_stock.quantity_healthy if src_stock else 0
        pending_outgoing = (
            AssetMovement.objects.filter(
                cafe=cafe,
                movement_type='transfer',
                status='pending_receipt',
                from_department=from_department,
                asset=asset,
                asset_unit__isnull=True,
            ).aggregate(total=Sum('quantity'))['total']
            or 0
        )
        available = src_healthy - pending_outgoing
        if available < qty:
            raise InsufficientAssetStockError(
                f'موجودی سالم قابل انتقال در بخش مبدأ کافی نیست (موجود: {available}، درخواستی: {qty}).'
            )

        val = asset.estimated_unit_value or Decimal('0')
        if require_handshake:
            return AssetMovement.objects.create(
                cafe=cafe,
                movement_type='transfer',
                status='pending_receipt',
                asset=asset,
                from_department=from_department,
                to_department=to_department,
                quantity=qty,
                condition_bucket='healthy',
                unit_value_snapshot=val,
                reason=reason,
                created_by=user,
            )

        src_stock.quantity_healthy -= qty
        src_stock.save(update_fields=['quantity_healthy', 'updated_at'])
        dst_stock, _ = DepartmentAssetStock.objects.select_for_update().get_or_create(
            department=to_department,
            asset=asset,
            defaults={'cafe': cafe, 'quantity_healthy': 0, 'quantity_damaged': 0},
        )
        dst_stock.quantity_healthy += qty
        dst_stock.save(update_fields=['quantity_healthy', 'updated_at'])

        return AssetMovement.objects.create(
            cafe=cafe,
            movement_type='transfer',
            status='approved',
            asset=asset,
            from_department=from_department,
            to_department=to_department,
            quantity=qty,
            condition_bucket='healthy',
            unit_value_snapshot=val,
            reason=reason,
            created_by=user,
            approved_by=user,
            received_by=user,
            approved_at=now,
            received_at=now,
        )


def _apply_transfer_locked(movement: AssetMovement, receiving_user) -> AssetMovement:
    now = timezone.now()
    if movement.asset_unit_id:
        unit = AssetUnit.objects.select_for_update().get(pk=movement.asset_unit_id)
        if movement.from_department_id and unit.department_id != movement.from_department_id:
            raise AssetStateError('پلاک دیگر در بخش مبدأ مستقر نیست.')
        if unit.status in ('lost', 'disposed'):
            raise AssetStateError('پلاک مفقودی یا اسقاط‌شده قابل تحویل نیست.')
        unit.department = movement.to_department
        unit.save(update_fields=['department', 'updated_at'])
        if movement.from_department_id:
            sync_serialized_department_stock(movement.from_department, movement.asset)
        if movement.to_department_id:
            sync_serialized_department_stock(movement.to_department, movement.asset)
    else:
        dept_ids = sorted(
            d_id for d_id in (movement.from_department_id, movement.to_department_id) if d_id
        )
        locked_stocks = {
            s.department_id: s
            for s in DepartmentAssetStock.objects.select_for_update().filter(
                department_id__in=dept_ids,
                asset=movement.asset,
            )
        }
        src_stock = locked_stocks.get(movement.from_department_id)
        if src_stock is None or src_stock.quantity_healthy < movement.quantity:
            raise InsufficientAssetStockError('موجودی سالم بخش مبدأ برای نهایی‌سازی انتقال کافی نیست.')
        src_stock.quantity_healthy -= movement.quantity
        src_stock.save(update_fields=['quantity_healthy', 'updated_at'])

        dst_stock = locked_stocks.get(movement.to_department_id)
        if dst_stock is None:
            dst_stock, _ = DepartmentAssetStock.objects.select_for_update().get_or_create(
                department=movement.to_department,
                asset=movement.asset,
                defaults={'cafe': movement.cafe, 'quantity_healthy': 0, 'quantity_damaged': 0},
            )
        dst_stock.quantity_healthy += movement.quantity
        dst_stock.save(update_fields=['quantity_healthy', 'updated_at'])

    movement.status = 'approved'
    movement.received_by = receiving_user
    movement.received_at = now
    movement.approved_by = receiving_user
    movement.approved_at = now
    movement.save(
        update_fields=['status', 'received_by', 'received_at', 'approved_by', 'approved_at']
    )
    return movement


def confirm_transfer_receipt(movement, receiving_user) -> AssetMovement:
    """تأیید تحویل انتقال توسط بخش مقصد و اعمال جابجایی موجودی."""
    _require_department_access(receiving_user, movement.cafe, movement.to_department)
    with transaction.atomic():
        locked = AssetMovement.objects.select_for_update().get(pk=movement.pk)
        if locked.movement_type != 'transfer' or locked.status != 'pending_receipt':
            raise AssetStateError('فقط انتقال‌های در وضعیت در انتظار تحویل قابل تأیید هستند.')
        result = _apply_transfer_locked(locked, receiving_user)
    if movement is not result:
        movement.refresh_from_db()
    return result


def reject_transfer(movement, user, reason='') -> AssetMovement:
    """رد انتقال در انتظار تحویل بدون تغییر در موجودی مبدأ یا مقصد."""
    _require_cafe_access(user, movement.cafe)
    has_dept_access = (
        user_can_manage_assets(user)
        or (movement.to_department and user_can_access_department_assets(user, movement.to_department))
        or (movement.from_department and user_can_access_department_assets(user, movement.from_department))
    )
    if not has_dept_access:
        raise AssetPermissionError('شما مجاز به رد کردن این انتقال نیستید.')

    with transaction.atomic():
        locked = AssetMovement.objects.select_for_update().get(pk=movement.pk)
        if locked.movement_type != 'transfer' or locked.status != 'pending_receipt':
            raise AssetStateError('فقط انتقال‌های در وضعیت در انتظار تحویل قابل رد هستند.')
        locked.status = 'rejected'
        locked.approved_by = user
        locked.approved_at = timezone.now()
        if reason:
            locked.reason = f'{locked.reason}\n{reason}'.strip() if locked.reason else reason
        locked.save(update_fields=['status', 'approved_by', 'approved_at', 'reason'])
    movement.refresh_from_db()
    return locked


def report_damage_or_disposal(
    cafe,
    department,
    asset,
    quantity,
    user,
    reason='',
    evidence_photo=None,
    asset_unit=None,
    auto_approve=False,
) -> AssetMovement:
    """گزارش شکستگی یا اسقاط؛ برای requester همیشه pending می‌ماند."""
    _require_department_access(user, cafe, department)
    if asset is None or asset.cafe_id != cafe.id:
        raise AssetValidationError('مال انتخاب‌شده متعلق به این کافه نیست.')

    should_approve = bool(auto_approve and user_can_manage_assets(user))
    now = timezone.now()

    with transaction.atomic():
        if asset.tracking_mode == 'serialized' or asset_unit is not None:
            if asset_unit is None:
                raise AssetValidationError('برای اقلام پلاکدار انتخاب پلاک الزامی است.')
            unit = AssetUnit.objects.select_for_update().get(pk=asset_unit.pk)
            if unit.cafe_id != cafe.id or unit.asset_id != asset.id:
                raise AssetValidationError('پلاک انتخاب‌شده با کالا یا کافه مطابقت ندارد.')
            if unit.department_id != department.id:
                raise AssetValidationError('این پلاک در بخش انتخاب‌شده مستقر نیست.')
            if unit.status in ('lost', 'disposed'):
                raise AssetStateError('این پلاک قبلاً اسقاط یا مفقود ثبت شده است.')

            val = unit.purchase_price or asset.estimated_unit_value or Decimal('0')
            movement = AssetMovement.objects.create(
                cafe=cafe,
                movement_type='disposal',
                status='approved' if should_approve else 'pending',
                asset=asset,
                asset_unit=unit,
                from_department=department,
                quantity=1,
                condition_bucket='healthy' if unit.status == 'active' else 'damaged',
                unit_value_snapshot=val,
                reason=reason,
                evidence_photo=evidence_photo,
                created_by=user,
                approved_by=user if should_approve else None,
                approved_at=now if should_approve else None,
            )
            if should_approve:
                unit.status = 'disposed'
                unit.save(update_fields=['status', 'updated_at'])
                sync_serialized_department_stock(department, asset)
            return movement

        qty = int(quantity)
        if qty <= 0:
            raise AssetValidationError('تعداد باید بزرگتر از صفر باشد.')

        stock = (
            DepartmentAssetStock.objects.select_for_update()
            .filter(department=department, asset=asset)
            .first()
        )
        if stock is None or (stock.quantity_healthy + stock.quantity_damaged) < qty:
            raise InsufficientAssetStockError('موجودی بخش برای ثبت شکستگی/اسقاط کافی نیست.')

        if stock.quantity_healthy >= qty:
            bucket = 'healthy'
        else:
            bucket = 'damaged'

        val = asset.estimated_unit_value or Decimal('0')
        movement = AssetMovement.objects.create(
            cafe=cafe,
            movement_type='disposal',
            status='approved' if should_approve else 'pending',
            asset=asset,
            from_department=department,
            quantity=qty,
            condition_bucket=bucket,
            unit_value_snapshot=val,
            reason=reason,
            evidence_photo=evidence_photo,
            created_by=user,
            approved_by=user if should_approve else None,
            approved_at=now if should_approve else None,
        )
        if should_approve:
            if bucket == 'healthy':
                stock.quantity_healthy -= qty
            else:
                stock.quantity_damaged -= qty
            stock.save(update_fields=['quantity_healthy', 'quantity_damaged', 'updated_at'])
        return movement


def approve_movement(movement, approver) -> AssetMovement:
    """تأیید و اعمال یک سند گردش در انتظار تأیید توسط مدیر."""
    _require_asset_manager(approver, movement.cafe)
    now = timezone.now()

    with transaction.atomic():
        locked = AssetMovement.objects.select_for_update().get(pk=movement.pk)
        if locked.status not in ('pending', 'pending_receipt'):
            raise AssetStateError('فقط گردش‌های در انتظار تأیید قابل تأیید هستند.')

        if locked.movement_type == 'transfer':
            result = _apply_transfer_locked(locked, approver)
            movement.refresh_from_db()
            return result

        if locked.movement_type == 'disposal':
            if locked.asset_unit_id:
                unit = AssetUnit.objects.select_for_update().get(pk=locked.asset_unit_id)
                if unit.status in ('lost', 'disposed'):
                    raise AssetStateError('این پلاک قبلاً اسقاط یا مفقود شده است.')
                unit.status = 'disposed'
                unit.save(update_fields=['status', 'updated_at'])
                dept = locked.from_department or unit.department
                if dept is not None:
                    sync_serialized_department_stock(dept, locked.asset)
            else:
                stock = (
                    DepartmentAssetStock.objects.select_for_update()
                    .filter(department=locked.from_department, asset=locked.asset)
                    .first()
                )
                if stock is None:
                    raise InsufficientAssetStockError('موجودی بخش یافت نشد.')
                if locked.condition_bucket == 'damaged':
                    if stock.quantity_damaged < locked.quantity:
                        raise InsufficientAssetStockError('موجودی معیوب بخش کافی نیست.')
                    stock.quantity_damaged -= locked.quantity
                else:
                    if stock.quantity_healthy >= locked.quantity:
                        stock.quantity_healthy -= locked.quantity
                    elif stock.quantity_damaged >= locked.quantity:
                        stock.quantity_damaged -= locked.quantity
                        locked.condition_bucket = 'damaged'
                    else:
                        raise InsufficientAssetStockError('موجودی بخش برای اسقاط کافی نیست.')
                stock.save(update_fields=['quantity_healthy', 'quantity_damaged', 'updated_at'])

        elif locked.movement_type in ('initial', 'addition'):
            if locked.to_department is None:
                raise AssetValidationError('بخش مقصد برای گردش ورود مشخص نیست.')
            stock, _ = DepartmentAssetStock.objects.select_for_update().get_or_create(
                department=locked.to_department,
                asset=locked.asset,
                defaults={'cafe': locked.cafe, 'quantity_healthy': 0, 'quantity_damaged': 0},
            )
            if locked.condition_bucket == 'damaged':
                stock.quantity_damaged += locked.quantity
            else:
                stock.quantity_healthy += locked.quantity
            stock.save(update_fields=['quantity_healthy', 'quantity_damaged', 'updated_at'])

        else:
            raise AssetStateError('این نوع گردش از این مسیر قابل تأیید نیست.')

        locked.status = 'approved'
        locked.approved_by = approver
        locked.approved_at = now
        locked.save(update_fields=['status', 'condition_bucket', 'approved_by', 'approved_at'])
    movement.refresh_from_db()
    return locked


def reject_movement(movement, approver, reason='') -> AssetMovement:
    """رد یک سند گردش در انتظار تأیید توسط مدیر."""
    _require_asset_manager(approver, movement.cafe)
    with transaction.atomic():
        locked = AssetMovement.objects.select_for_update().get(pk=movement.pk)
        if locked.status not in ('pending', 'pending_receipt'):
            raise AssetStateError('فقط گردش‌های در انتظار تأیید قابل رد هستند.')
        locked.status = 'rejected'
        locked.approved_by = approver
        locked.approved_at = timezone.now()
        if reason:
            locked.reason = f'{locked.reason}\n{reason}'.strip() if locked.reason else reason
        locked.save(update_fields=['status', 'approved_by', 'approved_at', 'reason'])
    movement.refresh_from_db()
    return locked


def update_unit_repair_status(
    unit,
    new_status,
    user,
    reason='',
    evidence_photo=None,
) -> AssetMovement:
    """تغییر وضعیت تعمیر / سلامت / اسقاط / مفقودی پلاک با ثبت گردش و همگام‌سازی بخش."""
    _require_asset_manager(user, unit.cafe)
    valid_statuses = {choice[0] for choice in AssetUnit.STATUS_CHOICES}
    if new_status not in valid_statuses:
        raise AssetValidationError('وضعیت پلاک نامعتبر است.')

    if new_status in ('needs_repair', 'in_repair'):
        movement_type = 'repair_out'
    elif new_status == 'active':
        movement_type = 'repair_in'
    else:
        movement_type = 'disposal'

    bucket = 'healthy' if new_status == 'active' else 'damaged'
    now = timezone.now()

    with transaction.atomic():
        locked_unit = AssetUnit.objects.select_for_update().get(pk=unit.pk)
        locked_unit.status = new_status
        locked_unit.save(update_fields=['status', 'updated_at'])

        movement = AssetMovement.objects.create(
            cafe=locked_unit.cafe,
            movement_type=movement_type,
            status='approved',
            asset=locked_unit.asset,
            asset_unit=locked_unit,
            from_department=locked_unit.department,
            to_department=locked_unit.department,
            quantity=1,
            condition_bucket=bucket,
            unit_value_snapshot=locked_unit.purchase_price or locked_unit.asset.estimated_unit_value or Decimal('0'),
            reason=reason,
            evidence_photo=evidence_photo,
            created_by=user,
            approved_by=user,
            approved_at=now,
        )

        if locked_unit.department_id:
            sync_serialized_department_stock(locked_unit.department, locked_unit.asset)

    unit.refresh_from_db()
    return movement


def create_audit_session(
    cafe,
    department,
    title,
    user,
    audit_type='full',
    blind_count=False,
    asset_ids=None,
    notes='',
) -> AssetAudit:
    """ایجاد جلسه انبارگردانی اموال بخش همراه با اسنپ‌شات موجودی و پلاک‌های فعلی."""
    _require_department_access(user, cafe, department)
    valid_types = {choice[0] for choice in AssetAudit.AUDIT_TYPE_CHOICES}
    if audit_type not in valid_types:
        raise AssetValidationError('نوع انبارگردانی نامعتبر است.')
    clean_title = (title or '').strip()
    if not clean_title:
        raise AssetValidationError('عنوان انبارگردانی الزامی است.')

    now = timezone.now()

    with transaction.atomic():
        audit = AssetAudit.objects.create(
            cafe=cafe,
            department=department,
            title=clean_title,
            audit_type=audit_type,
            status='in_progress',
            blind_count=bool(blind_count),
            notes=notes or '',
            created_by=user,
            started_at=now,
        )

        stocks_qs = DepartmentAssetStock.objects.select_related('asset').filter(
            cafe=cafe,
            department=department,
            asset__tracking_mode='quantity',
        )
        if asset_ids is not None:
            stocks_qs = stocks_qs.filter(asset_id__in=asset_ids)

        audit_items = []
        for stock in stocks_qs:
            if asset_ids is None and stock.quantity_healthy == 0 and stock.quantity_damaged == 0:
                continue
            audit_items.append(
                AssetAuditItem(
                    audit=audit,
                    asset=stock.asset,
                    expected_healthy=stock.quantity_healthy,
                    expected_damaged=stock.quantity_damaged,
                )
            )

        units_qs = AssetUnit.objects.select_related('asset').filter(
            cafe=cafe,
            department=department,
            status__in=('active', 'needs_repair', 'in_repair'),
        )
        if asset_ids is not None:
            units_qs = units_qs.filter(asset_id__in=asset_ids)

        for unit in units_qs:
            audit_items.append(
                AssetAuditItem(
                    audit=audit,
                    asset=unit.asset,
                    asset_unit=unit,
                    expected_healthy=1 if unit.status == 'active' else 0,
                    expected_damaged=1 if unit.status in ('needs_repair', 'in_repair') else 0,
                    expected_status=unit.status,
                )
            )

        if audit_items:
            AssetAuditItem.objects.bulk_create(audit_items)

    return audit


def save_audit_item_count(
    audit_item,
    user,
    counted_healthy=None,
    counted_damaged=None,
    counted_status='',
    note='',
    evidence_photo=None,
) -> AssetAuditItem:
    """ثبت شمارش یک ردیف در انبارگردانی (فقط در وضعیت in_progress یا recount_requested)."""
    _require_department_access(user, audit_item.audit.cafe, audit_item.audit.department)

    with transaction.atomic():
        audit = AssetAudit.objects.select_for_update().get(pk=audit_item.audit_id)
        if audit.status not in ('in_progress', 'recount_requested'):
            raise AssetStateError('ثبت شمارش فقط در وضعیت در حال شمارش یا بازشماری مجاز است.')

        item = AssetAuditItem.objects.select_for_update().select_related('asset', 'asset_unit').get(
            pk=audit_item.pk
        )

        if item.asset_unit_id or item.asset.tracking_mode == 'serialized':
            valid_conditions = {choice[0] for choice in AssetAuditItem.CONDITION_CHOICES}
            status_val = (counted_status or '').strip()
            if not status_val:
                if counted_healthy == 1:
                    status_val = 'active'
                elif counted_damaged == 1:
                    status_val = 'needs_repair'
                elif counted_healthy == 0 and counted_damaged == 0:
                    status_val = 'lost'
            if status_val not in valid_conditions:
                raise AssetValidationError('وضعیت شمارش‌شده برای قلم پلاکدار نامعتبر است.')

            item.counted_status = status_val
            if status_val == 'active':
                item.counted_healthy = 1
                item.counted_damaged = 0
            elif status_val in ('needs_repair', 'in_repair'):
                item.counted_healthy = 0
                item.counted_damaged = 1
            else:
                item.counted_healthy = 0
                item.counted_damaged = 0
        else:
            ch = int(counted_healthy) if counted_healthy is not None else 0
            cd = int(counted_damaged) if counted_damaged is not None else 0
            if ch < 0 or cd < 0:
                raise AssetValidationError('مقادیر شمارش‌شده نمی‌توانند منفی باشند.')
            item.counted_healthy = ch
            item.counted_damaged = cd

        item.is_counted = True
        item.needs_recount = False
        item.counted_at = timezone.now()
        if note:
            item.note = note
        if evidence_photo is not None:
            item.evidence_photo = evidence_photo
        item.save()

        audit.counted_by = user
        audit.save(update_fields=['counted_by', 'updated_at'])

    audit_item.refresh_from_db()
    return item


def add_unexpected_audit_item(
    audit,
    asset,
    counted_healthy,
    user,
    counted_damaged=0,
    note='',
    evidence_photo=None,
) -> AssetAuditItem:
    """افزودن قلم یافت‌شده خارج از لیست مورد انتظار در حین انبارگردانی."""
    _require_department_access(user, audit.cafe, audit.department)
    if asset is None or asset.cafe_id != audit.cafe_id:
        raise AssetValidationError('مال انتخاب‌شده متعلق به کافه این انبارگردانی نیست.')

    ch = int(counted_healthy)
    cd = int(counted_damaged)
    if ch < 0 or cd < 0:
        raise AssetValidationError('تعداد نمی‌تواند منفی باشد.')

    with transaction.atomic():
        locked_audit = AssetAudit.objects.select_for_update().get(pk=audit.pk)
        if locked_audit.status not in ('in_progress', 'recount_requested'):
            raise AssetStateError('افزودن قلم فقط در حین شمارش یا بازشماری مجاز است.')

        item = AssetAuditItem.objects.create(
            audit=locked_audit,
            asset=asset,
            expected_healthy=0,
            expected_damaged=0,
            counted_healthy=ch,
            counted_damaged=cd,
            is_counted=True,
            needs_recount=False,
            is_unexpected_item=True,
            note=note or '',
            evidence_photo=evidence_photo,
            counted_at=timezone.now(),
        )
        locked_audit.counted_by = user
        locked_audit.save(update_fields=['counted_by', 'updated_at'])

    return item


def submit_audit(audit, user) -> AssetAudit:
    """ارسال جلسه انبارگردانی برای بررسی و تأیید."""
    _require_department_access(user, audit.cafe, audit.department)
    with transaction.atomic():
        locked = AssetAudit.objects.select_for_update().get(pk=audit.pk)
        if locked.status not in ('in_progress', 'recount_requested'):
            raise AssetStateError('این انبارگردانی در وضعیت قابل ارسال نیست.')
        locked.status = 'submitted'
        locked.submitted_at = timezone.now()
        if locked.counted_by_id is None:
            locked.counted_by = user
        locked.save(update_fields=['status', 'submitted_at', 'counted_by', 'updated_at'])
    audit.refresh_from_db()
    return locked


def request_audit_recount(audit, reviewer, item_ids=None, review_notes='') -> AssetAudit:
    """ارجاع انبارگردانی برای بازشماری و علامت‌گذاری ردیف‌های مشخص‌شده."""
    _require_asset_manager(reviewer, audit.cafe)
    with transaction.atomic():
        locked = AssetAudit.objects.select_for_update().get(pk=audit.pk)
        if locked.status != 'submitted':
            raise AssetStateError('فقط انبارگردانی ارسال‌شده قابل ارجاع برای بازشماری است.')

        items_qs = locked.items.select_for_update()
        if item_ids is not None:
            items_qs = items_qs.filter(id__in=item_ids)
        items_qs.update(needs_recount=True)

        locked.status = 'recount_requested'
        locked.reviewed_by = reviewer
        if review_notes:
            locked.review_notes = review_notes
        locked.save(update_fields=['status', 'reviewed_by', 'review_notes', 'updated_at'])
    audit.refresh_from_db()
    return locked


def approve_audit_and_apply_adjustments(audit, reviewer, review_notes='') -> list[AssetMovement]:
    """تأیید نهایی انبارگردانی و اعمال تعدیل‌های موجودی به همراه ثبت AssetMovement."""
    _require_asset_manager(reviewer, audit.cafe)
    movements: list[AssetMovement] = []
    now = timezone.now()

    with transaction.atomic():
        locked = AssetAudit.objects.select_for_update().get(pk=audit.pk)
        if locked.status != 'submitted':
            raise AssetStateError('فقط انبارگردانی در وضعیت ارسال‌شده قابل تأیید نهایی است.')

        items = list(
            locked.items.select_for_update()
            .select_related('asset', 'asset_unit')
            .order_by('id')
        )

        for item in items:
            if not item.is_counted:
                continue

            if item.asset_unit_id:
                if item.counted_status and item.counted_status != item.expected_status:
                    unit = AssetUnit.objects.select_for_update().get(pk=item.asset_unit_id)
                    unit.status = item.counted_status
                    unit.save(update_fields=['status', 'updated_at'])
                    sync_serialized_department_stock(locked.department, item.asset)

                    qty_val = -1 if item.counted_status == 'lost' else 1
                    bucket = 'healthy' if item.counted_status == 'active' else 'damaged'
                    unit_val = unit.purchase_price or item.asset.estimated_unit_value or Decimal('0')

                    mv = AssetMovement.objects.create(
                        cafe=locked.cafe,
                        movement_type='audit_adjustment',
                        status='approved',
                        asset=item.asset,
                        asset_unit=unit,
                        from_department=locked.department,
                        to_department=locked.department,
                        quantity=qty_val,
                        condition_bucket=bucket,
                        unit_value_snapshot=unit_val,
                        reason=item.note or f'تعدیل انبارگردانی ({locked.title}): {item.expected_status} → {item.counted_status}',
                        evidence_photo=item.evidence_photo,
                        audit=locked,
                        created_by=reviewer,
                        approved_by=reviewer,
                        approved_at=now,
                    )
                    movements.append(mv)
                continue

            vh = item.variance_healthy
            vd = item.variance_damaged
            if vh == 0 and vd == 0:
                continue

            stock, _ = DepartmentAssetStock.objects.select_for_update().get_or_create(
                department=locked.department,
                asset=item.asset,
                defaults={'cafe': locked.cafe, 'quantity_healthy': 0, 'quantity_damaged': 0},
            )
            if item.counted_healthy is not None:
                stock.quantity_healthy = item.counted_healthy
            if item.counted_damaged is not None:
                stock.quantity_damaged = item.counted_damaged
            stock.save(update_fields=['quantity_healthy', 'quantity_damaged', 'updated_at'])

            unit_val = item.asset.estimated_unit_value or Decimal('0')
            base_reason = item.note or f'تعدیل انبارگردانی ({locked.title})'

            if vh != 0:
                mv_h = AssetMovement.objects.create(
                    cafe=locked.cafe,
                    movement_type='audit_adjustment',
                    status='approved',
                    asset=item.asset,
                    from_department=locked.department if vh < 0 else None,
                    to_department=locked.department if vh > 0 else None,
                    quantity=vh,
                    condition_bucket='healthy',
                    unit_value_snapshot=unit_val,
                    reason=base_reason,
                    evidence_photo=item.evidence_photo,
                    audit=locked,
                    created_by=reviewer,
                    approved_by=reviewer,
                    approved_at=now,
                )
                movements.append(mv_h)

            if vd != 0:
                mv_d = AssetMovement.objects.create(
                    cafe=locked.cafe,
                    movement_type='audit_adjustment',
                    status='approved',
                    asset=item.asset,
                    from_department=locked.department if vd < 0 else None,
                    to_department=locked.department if vd > 0 else None,
                    quantity=vd,
                    condition_bucket='damaged',
                    unit_value_snapshot=unit_val,
                    reason=base_reason,
                    evidence_photo=item.evidence_photo,
                    audit=locked,
                    created_by=reviewer,
                    approved_by=reviewer,
                    approved_at=now,
                )
                movements.append(mv_d)

        locked.status = 'approved'
        locked.reviewed_by = reviewer
        locked.approved_at = now
        if review_notes:
            locked.review_notes = review_notes
        locked.save(update_fields=['status', 'reviewed_by', 'approved_at', 'review_notes', 'updated_at'])

    audit.refresh_from_db()
    return movements


def create_replacement_purchase_request(
    cafe,
    department,
    user,
    shortage_items: list[dict],
) -> PurchaseRequest:
    """ایجاد درخواست خرید جایگزین در سیستم خرید فعلی از روی کسری‌های اموال بخش."""
    _require_department_access(user, cafe, department)
    if not shortage_items:
        raise AssetValidationError('لیست اقلام دارای کسری خالی است.')

    prepared_items = []
    for entry in shortage_items:
        stock_obj = entry.get('stock')
        asset = entry.get('asset')
        if asset is None and stock_obj is not None:
            asset = stock_obj.asset
        if asset is None and entry.get('asset_id'):
            asset = AssetCatalog.objects.filter(pk=entry['asset_id'], cafe=cafe).first()
            if asset is None:
                raise AssetValidationError('کالای اموال یافت نشد یا متعلق به این کافه نیست.')
        if asset is not None and asset.cafe_id != cafe.id:
            raise AssetValidationError('کالای اموال باید متعلق به همین کافه باشد.')

        raw_qty = entry.get('quantity')
        if raw_qty is None:
            raw_qty = entry.get('shortage_quantity')
        if raw_qty is None and stock_obj is not None:
            raw_qty = stock_obj.shortage_quantity

        qty = Decimal(str(raw_qty or 0))
        if qty <= 0:
            continue

        item_name = (entry.get('name') or (asset.name if asset else '')).strip()[:100]
        if not item_name:
            raise AssetValidationError('نام قلم برای درخواست خرید مشخص نیست.')

        unit_label = (entry.get('unit') or 'عدد').strip()[:20] or 'عدد'
        note_text = (entry.get('note') or entry.get('description') or entry.get('reason') or '').strip()
        desc_parts = [f'تأمین کسری اموال بخش {department.name}']
        if asset is not None:
            desc_parts.append(f'کد مال: {asset.code}')
        if note_text:
            desc_parts.append(note_text)

        prepared_items.append({
            'name': item_name,
            'quantity': qty,
            'unit': unit_label,
            'description': ' | '.join(desc_parts),
        })

    if not prepared_items:
        raise AssetValidationError('هیچ قلم با مقدار کسری معتبر برای ایجاد درخواست خرید وجود ندارد.')

    with transaction.atomic():
        pr = PurchaseRequest.objects.create(
            cafe=cafe,
            user=user,
            source='requester',
            status='pending_manager_approval',
        )
        PurchaseItem.objects.bulk_create([
            PurchaseItem(
                request=pr,
                product=None,
                name=it['name'],
                quantity=it['quantity'],
                unit=it['unit'],
                description=it['description'],
            )
            for it in prepared_items
        ])

    return pr
