#!/bin/bash

# اسکریپت اعتبارسنجی نصب Production

echo "🔍 اعتبارسنجی نصب Syntel..."
echo "======================================"

# رنگ‌ها
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

check() {
    if eval "$2" &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} $1"
        ((FAILED++))
    fi
}

# بررسی کانتینرها
echo ""
echo "📦 بررسی کانتینرها:"
check "Database (db) در حال اجرا" "docker-compose ps | grep 'db' | grep 'Up'"
check "Redis در حال اجرا" "docker-compose ps | grep 'redis' | grep 'Up'"
check "Web در حال اجرا" "docker-compose ps | grep 'web' | grep 'Up'"
check "Nginx در حال اجرا" "docker-compose ps | grep 'nginx' | grep 'Up'"

# بررسی Health Checks
echo ""
echo "🏥 بررسی سلامت سرویس‌ها:"
check "Database Health" "docker-compose exec -T db pg_isready -U syntel_user"
check "Redis Health" "docker-compose exec -T redis redis-cli ping"
check "Web Health" "docker-compose exec -T web curl -f http://localhost:8000/health/"

# بررسی دیتابیس
echo ""
echo "💾 بررسی دیتابیس:"
check "اتصال به دیتابیس" "docker-compose exec -T db psql -U syntel_user -d syntel_prod -c 'SELECT 1'"
check "Migrations اجرا شده" "docker-compose exec -T web python manage.py showmigrations | grep -q '\[X\]'"

# بررسی Static Files
echo ""
echo "📁 بررسی Static Files:"
check "Static files جمع‌آوری شده" "docker-compose exec -T web ls staticfiles/admin/css/base.css"

# بررسی Superuser
echo ""
echo "👤 بررسی کاربران:"
SUPERUSER_EXISTS=$(docker-compose exec -T web python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).exists())" 2>&1 | tail -1)
if [ "$SUPERUSER_EXISTS" == "True" ]; then
    echo -e "${GREEN}✓${NC} حداقل یک Superuser وجود دارد"
    ((PASSED++))
else
    echo -e "${RED}✗${NC} Superuser وجود ندارد"
    ((FAILED++))
fi

# بررسی متغیرهای محیطی
echo ""
echo "⚙️  بررسی تنظیمات:"
if [ -f .env ]; then
    echo -e "${GREEN}✓${NC} فایل .env وجود دارد"
    ((PASSED++))
    
    # بررسی متغیرهای مهم
    if grep -q "DJANGO_SECRET_KEY=your-super-secret-key" .env; then
        echo -e "${YELLOW}⚠${NC} SECRET_KEY هنوز تغییر نکرده است!"
    fi
    
    if grep -q "DJANGO_DEBUG=True" .env; then
        echo -e "${YELLOW}⚠${NC} DEBUG روی True است (برای Production امن نیست!)"
    fi
else
    echo -e "${RED}✗${NC} فایل .env وجود ندارد"
    ((FAILED++))
fi

# بررسی پورت‌ها
echo ""
echo "🔌 بررسی پورت‌ها:"
check "پورت 80 (HTTP) باز است" "nc -z localhost 80"
check "پورت 443 (HTTPS) باز است" "nc -z localhost 443"

# خلاصه نتیجه
echo ""
echo "======================================"
echo "📊 نتیجه اعتبارسنجی:"
echo "======================================"
echo -e "   ${GREEN}موفق:${NC} $PASSED"
echo -e "   ${RED}ناموفق:${NC} $FAILED"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ همه تست‌ها موفق بود!${NC}"
    echo "سیستم آماده استفاده است. 🎉"
    exit 0
else
    echo -e "${RED}✗ برخی تست‌ها ناموفق بودند!${NC}"
    echo "لطفاً لاگ‌ها را بررسی کنید:"
    echo "   docker-compose logs"
    exit 1
fi
