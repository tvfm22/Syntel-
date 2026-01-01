#!/usr/bin/env python
"""
بررسی تمپلیت finance_dashboard
"""
import os

print("🔍 بررسی تمپلیت‌های finance_dashboard...")
print("=" * 60)

# مسیر تمپلیت‌ها
frontend_template = "frontend/templates/finance_dashboard.html"
purchases_template = "purchases/templates/finance_dashboard.html"

# بررسی وجود فایل‌ها
print("\n1️⃣ بررسی فایل‌ها:")
if os.path.exists(frontend_template):
    print(f"   ✅ {frontend_template} - موجود است")
    with open(frontend_template, 'r', encoding='utf-8') as f:
        content = f.read()
        if 'مدیریت انبار' in content:
            print(f"   ✅ بخش 'مدیریت انبار' در {frontend_template} یافت شد")
        else:
            print(f"   ⚠️ بخش 'مدیریت انبار' در {frontend_template} یافت نشد")
else:
    print(f"   ❌ {frontend_template} - یافت نشد")

print()
if os.path.exists(purchases_template):
    print(f"   ✅ {purchases_template} - موجود است")
    with open(purchases_template, 'r', encoding='utf-8') as f:
        content = f.read()
        if 'مدیریت انبار' in content:
            print(f"   ✅ بخش 'مدیریت انبار' در {purchases_template} یافت شد")
        else:
            print(f"   ⚠️ بخش 'مدیریت انبار' در {purchases_template} یافت نشد")
else:
    print(f"   ❌ {purchases_template} - یافت نشد")

print("\n" + "=" * 60)
print("✅ بررسی تمپلیت‌ها تکمیل شد!")
print("\n🌐 حالا می‌توانید داشبورد مالی را مشاهده کنید:")
print("   http://localhost:8000/finance-dashboard/")
print("=" * 60)
