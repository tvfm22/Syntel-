from __future__ import annotations

from ..models import Invoice
from ..models import Product
from ..models import PurchaseOrder
from ..models import PurchaseRequest
from ..models import StockItem
from .helpers import custom_login_required
from .helpers import get_cafe_from_request
from .helpers import role_required
from decimal import Decimal
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import render
import io
import os
import openpyxl


@custom_login_required
@role_required(["purchasing", "manager"])
def reports_dashboard(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        pr_qs = PurchaseRequest.objects.none()
        inv_qs = Invoice.objects.none()
        po_qs = PurchaseOrder.objects.none()
    else:
        pr_qs = PurchaseRequest.objects.filter(cafe=cafe)
        inv_qs = Invoice.objects.filter(cafe=cafe)
        po_qs = PurchaseOrder.objects.filter(request__cafe=cafe)
    stats = {
        "total_requests": pr_qs.count(),
        "completed_orders": po_qs.filter(status="completed").count(),
        "paid_invoices": inv_qs.filter(status="paid").count(),
    }
    return render(request, "reports_dashboard.html", {"stats": stats})


@custom_login_required
@role_required(["purchasing", "manager"])
def export_to_excel(request):
    import openpyxl

    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.filter(cafe=cafe).select_related("user").order_by("-created_at")[:500] if cafe else PurchaseRequest.objects.none()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Purchase Requests"
    ws.append(["Request ID", "Request Number", "User", "Status", "Created At"])
    for pr in pr_qs:
        ws.append([
            pr.id,
            pr.request_number,
            pr.user.username if pr.user else "",
            pr.status,
            pr.created_at.strftime("%Y-%m-%d %H:%M") if pr.created_at else "",
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="reports.xlsx"'
    return response


def _shape_pdf_text(text: str) -> str:
    """Reshape and reorder Persian/Arabic text for ReportLab when libraries are available."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except ImportError:
        return str(text)


def _register_persian_pdf_font() -> str:
    """Register a TTF font supporting Persian glyphs if available; fallback to Helvetica."""
    from django.conf import settings
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = "PersianFont"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name

    base_dir = str(getattr(settings, "BASE_DIR", ""))
    candidates = [
        os.path.join(base_dir, "frontend", "static", "fonts", "Vazirmatn-Regular.ttf"),
        os.path.join(base_dir, "frontend", "static", "fonts", "Vazir.ttf"),
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
            except Exception:
                continue
    return "Helvetica"


@custom_login_required
@role_required(["purchasing", "manager"])
def export_to_pdf(request):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    cafe = get_cafe_from_request(request)
    pr_qs = PurchaseRequest.objects.filter(cafe=cafe).order_by("-created_at")[:100] if cafe else PurchaseRequest.objects.none()

    font_name = _register_persian_pdf_font()
    title_font = font_name if font_name != "Helvetica" else "Helvetica-Bold"

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = A4[1] - 50
    c.setFont(title_font, 14)
    c.drawString(50, y, _shape_pdf_text(f"Purchase Requests Report ({cafe.code if cafe else 'N/A'})"))
    y -= 28
    c.setFont(font_name, 10)
    for pr in pr_qs:
        c.drawString(50, y, _shape_pdf_text(f"#{pr.request_number} - status: {pr.status}"))
        y -= 16
        if y < 50:
            c.showPage()
            c.setFont(font_name, 10)
            y = A4[1] - 50
    c.save()

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="reports.pdf"'
    return response


@custom_login_required
@role_required(["purchasing", "manager"])
def export_enhanced_excel(request):
    # Reuse base export for simplicity
    return export_to_excel(request)


@custom_login_required
@role_required(["purchasing", "manager"])
def export_analytics_to_pdf(request, analytics_type):
    return export_to_pdf(request)


@custom_login_required
@role_required(["purchasing", "manager"])
def purchase_reports(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        pr_qs = PurchaseRequest.objects.none()
        po_qs = PurchaseOrder.objects.none()
    else:
        pr_qs = PurchaseRequest.objects.filter(cafe=cafe).select_related("user").order_by("-created_at")
        po_qs = PurchaseOrder.objects.filter(request__cafe=cafe).select_related("request", "supplier").order_by("-created_at")
    stats = {
        "total_requests": pr_qs.count(),
        "total_orders": po_qs.count(),
        "completed_orders": po_qs.filter(status="completed").count(),
        "pending_orders": po_qs.exclude(status="completed").count(),
    }
    return render(request, "purchase_reports.html", {
        "requests": pr_qs[:100],
        "orders": po_qs[:100],
        "stats": stats,
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def payment_analytics(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        inv_qs = Invoice.objects.none()
    else:
        inv_qs = Invoice.objects.filter(cafe=cafe).select_related("supplier").order_by("-invoice_date")
    total_amount = inv_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    paid_amount = inv_qs.filter(status="paid").aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    stats = {
        "total_invoices": inv_qs.count(),
        "paid_invoices": inv_qs.filter(status="paid").count(),
        "unpaid_invoices": inv_qs.exclude(status="paid").count(),
        "total_amount": total_amount,
        "paid_amount": paid_amount,
    }
    return render(request, "payment_analytics.html", {
        "invoices": inv_qs[:100],
        "stats": stats,
    })


@custom_login_required
@role_required(["purchasing", "manager"])
def inventory_reports(request):
    cafe = get_cafe_from_request(request)
    if cafe is None:
        stock_qs = StockItem.objects.none()
        product_count = 0
    else:
        stock_qs = StockItem.objects.filter(warehouse__cafe=cafe).select_related(
            "product", "warehouse", "unit"
        ).order_by("product__name")
        product_count = Product.objects.filter(cafe=cafe, is_active=True).count()
    stock_items = list(stock_qs[:200])
    low_stock_items = [
        s for s in stock_items
        if s.product and s.product.min_stock and s.quantity <= s.product.min_stock
    ]
    stats = {
        "total_products": product_count,
        "total_stock_items": stock_qs.count(),
        "low_stock_count": len(low_stock_items),
    }
    return render(request, "inventory_reports.html", {
        "stock_items": stock_items,
        "low_stock_items": low_stock_items,
        "stats": stats,
    })
