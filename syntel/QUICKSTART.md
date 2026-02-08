# 🚀 راهنمای سریع دیپلوی Syntel

## آی‌پی سرور: 45.159.150.199

### ⚡ شروع سریع (3 مرحله)

#### 1️⃣ نصب Docker و تنظیم سرور
```bash
cd syntel
sudo ./server_setup.sh
```

#### 2️⃣ بررسی تنظیمات
```bash
# فایل .env را بررسی کنید
nano .env

# مطمئن شوید که این تنظیمات درست هستند:
# - DJANGO_SECRET_KEY (کلید امنیتی منحصر به فرد)
# - DB_PASSWORD (رمز عبور قوی)
# - EMAIL_* (تنظیمات ایمیل)
```

#### 3️⃣ اجرای دیپلوی
```bash
./deploy_production.sh
```

---

## 📋 دستورات مهم

### دیپلوی و راه‌اندازی
```bash
./deploy_production.sh      # دیپلوی کامل
./quick_deploy.sh           # آپدیت سریع
./health_check.sh           # بررسی سلامت سیستم
```

### مدیریت
```bash
docker-compose ps           # وضعیت سرویس‌ها
docker-compose logs -f      # مشاهده لاگ‌ها
docker-compose restart      # ری‌استارت همه
```

### پشتیبان‌گیری
```bash
./backup.sh                 # ایجاد بکاپ
./restore.sh backup.sql.gz  # بازیابی از بکاپ
```

---

## 🌐 دسترسی به سیستم

**آدرس وب:** http://45.159.150.199

**اطلاعات ورود اولیه:**
- 👤 نام کاربری: `admin`
- 🔑 رمز عبور: `admin123`

⚠️ **مهم**: بلافاصله رمز عبور را تغییر دهید!

---

## 🔧 عیب‌یابی سریع

### سرویس web خطا می‌دهد
```bash
docker-compose logs web
docker-compose restart web
```

### دیتابیس متصل نمی‌شود
```bash
docker-compose logs db
docker-compose restart db
```

### نمایش صفحات با مشکل است
```bash
docker-compose exec web python manage.py collectstatic --noinput
docker-compose restart nginx
```

---

## 📚 اسناد کامل

برای اطلاعات بیشتر:
- [راهنمای کامل دیپلوی](DEPLOYMENT.md)
- [مستندات فنی](../TECHNICAL_DOCUMENTATION.md)
- [راهنمای رفع مشکل](../TROUBLESHOOTING_GUIDE.md)

---

## 📞 کمک سریع

اگر Docker نصب نیست:
```bash
sudo ./server_setup.sh
```

اگر دیپلوی مشکل دارد:
```bash
docker-compose down
docker system prune -f
./deploy_production.sh
```

اگر سیستم کند است:
```bash
./health_check.sh
docker stats
```

---

## ✅ چک‌لیست پیش از دیپلوی

- [ ] Docker و Docker Compose نصب است
- [ ] فایل `.env` تنظیم شده است
- [ ] SECRET_KEY منحصر به فرد است
- [ ] رمز دیتابیس قوی است
- [ ] IP سرور در ALLOWED_HOSTS است
- [ ] پورت‌های 80 و 443 باز هستند
- [ ] فضای دیسک کافی است (حداقل 10GB)

---

## 🎯 مراحل بعد از دیپلوی

1. ✅ تست سیستم: http://45.159.150.199
2. 🔑 تغییر رمز ادمین
3. 👥 ایجاد کاربران
4. 📊 بررسی داشبورد
5. 🔄 تنظیم بکاپ خودکار
6. 🔒 فعال‌سازی HTTPS (اختیاری)

---

**آخرین بروزرسانی:** 8 فوریه 2026
