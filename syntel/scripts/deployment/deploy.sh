#!/bin/bash

# 🚀 اسکریپت استقرار خودکار Syntel Production
# این اسکریپت تمام مراحل deployment را به صورت خودکار انجام می‌دهد

set -e  # خروج در صورت بروز خطا

echo "🚀 شروع فرآیند استقرار Syntel..."
echo "======================================"

# رنگ‌ها برای خروجی
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# تابع برای نمایش پیام‌ها
info() {
    echo -e "${GREEN}✓${NC} $1"
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
    exit 1
}

# بررسی وجود Docker
if ! command -v docker &> /dev/null; then
    error "Docker نصب نشده است! لطفاً ابتدا Docker را نصب کنید."
fi

if ! command -v docker-compose &> /dev/null; then
    error "Docker Compose نصب نشده است!"
fi

info "Docker و Docker Compose یافت شد"

# بررسی وجود فایل .env
if [ ! -f .env ]; then
    warn "فایل .env یافت نشد! کپی از .env.example..."
    cp .env.example .env
    warn "لطفاً فایل .env را ویرایش کنید و مقادیر واقعی را وارد کنید"
    read -p "آیا می‌خواهید ادامه دهید؟ (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    info "فایل .env یافت شد"
fi

# توقف کانتینرهای قبلی (اگر وجود دارند)
info "توقف کانتینرهای قبلی..."
docker-compose down || true

# پاک کردن volumes قدیمی (اختیاری - فقط در نصب اولیه)
read -p "آیا می‌خواهید دیتابیس قبلی را پاک کنید؟ (فقط برای نصب اولیه) (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    warn "پاک کردن volumes قدیمی..."
    docker-compose down -v
fi

# ساخت و اجرای کانتینرها
info "ساخت و اجرای کانتینرها..."
docker-compose up -d --build

# انتظار برای آماده شدن دیتابیس
info "انتظار برای آماده شدن دیتابیس..."
sleep 10

# بررسی وضعیت کانتینرها
info "بررسی وضعیت کانتینرها..."
docker-compose ps

# اجرای Migrations
info "اجرای migrations..."
docker-compose exec -T web python manage.py migrate --noinput

# جمع‌آوری Static Files
info "جمع‌آوری Static Files..."
docker-compose exec -T web python manage.py collectstatic --noinput

# ایجاد Superuser (فقط اگر وجود نداشته باشد)
info "بررسی Superuser..."
docker-compose exec -T web python manage.py shell << EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(is_superuser=True).exists():
    print("ایجاد superuser پیش‌فرض...")
    User.objects.create_superuser('admin', 'admin@syntel.local', 'Admin@123')
    print("✓ Superuser ایجاد شد: admin / Admin@123")
    print("⚠ لطفاً رمز عبور را در پنل ادمین تغییر دهید!")
else:
    print("✓ Superuser وجود دارد")
EOF

# بررسی سلامت سرویس‌ها
info "بررسی سلامت سرویس‌ها..."
sleep 5

# بررسی وضعیت web service
if docker-compose exec -T web curl -f http://localhost:8000/health/ &> /dev/null; then
    info "سرویس Web در حال اجرا است ✓"
else
    warn "سرویس Web پاسخ نمی‌دهد، بررسی لاگ‌ها..."
    docker-compose logs web --tail=50
fi

# نمایش اطلاعات
echo ""
echo "======================================"
echo -e "${GREEN}✓ استقرار با موفقیت انجام شد!${NC}"
echo "======================================"
echo ""
echo "📋 اطلاعات دسترسی:"
echo "   🌐 وب سایت: http://localhost (یا دامنه شما)"
echo "   🔐 پنل ادمین: http://localhost/admin/"
echo "   👤 نام کاربری پیش‌فرض: admin"
echo "   🔑 رمز عبور پیش‌فرض: Admin@123"
echo ""
echo "⚠️  هشدارهای امنیتی:"
echo "   1. حتماً رمز عبور ادمین را تغییر دهید"
echo "   2. فایل .env را محرمانه نگه دارید"
echo "   3. SECRET_KEY را با یک مقدار تصادفی جایگزین کنید"
echo "   4. ALLOWED_HOSTS را مطابق دامنه خود تنظیم کنید"
echo ""
echo "📊 دستورات مفید:"
echo "   مشاهده لاگ‌ها:        docker-compose logs -f"
echo "   ری‌استارت:            docker-compose restart"
echo "   توقف:                 docker-compose down"
echo "   وضعیت:                docker-compose ps"
echo ""
