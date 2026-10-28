# راهنمای همگام‌سازی زمان سرور

## زمان فعلی Django
Django از زمان سیستم‌عامل استفاده می‌کند و آن را به timezone تنظیم شده (`Asia/Tehran`) تبدیل می‌کند.

## منبع زمان در حال حاضر
- **Server Time**: زمان سیستم‌عامل (Windows/Linux)
- **Django Timezone**: Asia/Tehran (تنظیم شده در settings.py)
- **Field Type**: `auto_now_add=True` - زمان ایجاد رکورد را خودکار ثبت می‌کند

## تنظیم همگام‌سازی زمان در ویندوز

### 1. تنظیم سرور زمان ایران
```powershell
# باز کردن PowerShell به عنوان Administrator
w32tm /config /manualpeerlist:"ntp1.inrim.it ntp2.inrim.it" /syncfromflags:manual /reliable:yes /update
w32tm /resync
```

### 2. استفاده از سرور زمان ایران
می‌توانید از سرورهای NTP ایران استفاده کنید:
```
- ntp1.inrim.it
- ntp2.inrim.it  
- time.google.com
```

### 3. بررسی وضعیت همگام‌سازی
```powershell
w32tm /query /status
w32tm /query /peers
```

## تنظیم در Linux/Ubuntu
```bash
# نصب NTP
sudo apt-get install ntp

# تنظیم سرور زمان
sudo nano /etc/ntp.conf

# اضافه کردن این خطوط:
server ntp1.inrim.it
server ntp2.inrim.it

# راه‌اندازی مجدد
sudo systemctl restart ntp
```

## استفاده از API زمان آنلاین (اختیاری)
اگر می‌خواهید از منبع خارجی استفاده کنید:

### 1. نصب کتابانه
```bash
pip install pytz requests
```

### 2. ساخت Custom Manager (در صورت نیاز)
```python
# در models.py یا utils.py
import requests
from django.utils import timezone
import pytz

def get_exact_iran_time():
    """دریافت زمان دقیق از API آنلاین"""
    try:
        # استفاده از WorldTimeAPI
        response = requests.get('http://worldtimeapi.org/api/timezone/Asia/Tehran', timeout=2)
        if response.status_code == 200:
            data = response.json()
            return timezone.datetime.fromisoformat(data['datetime'])
    except:
        pass
    
    # در صورت خطا، از زمان سیستم استفاده کن
    return timezone.now()
```

## توصیه
**برای production:**
- همگام‌سازی خودکار NTP کافی است
- نیازی به API خارجی نیست
- Django خودش timezone را مدیریت می‌کند

**وضعیت فعلی:**
✅ تاریخ و ساعت خودکار ثبت می‌شود
✅ Timezone تهران تنظیم شده است
✅ دقت در حد میلی‌ثانیه (کافی برای سیستم‌های کسب‌وکار)

## تست زمان فعلی Django
```python
# در Django shell
python manage.py shell

from django.utils import timezone
print(timezone.now())  # زمان UTC
print(timezone.localtime())  # زمان تهران
```
