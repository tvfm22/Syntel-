#!/usr/bin/env python
"""
اسکریپت آپدیت URL های template ها برای پشتیبانی از چند کافه‌ای

این اسکریپت همه template ها را اسکن می‌کند و:
1. {% url 'name' %} را به {% url 'name' cafe_code=cafe_code %} تبدیل می‌کند
2. {% url 'name' param %} را به {% url 'name' param cafe_code=cafe_code %} تبدیل می‌کند
"""

import os
import re
from pathlib import Path

# مسیرهای template
TEMPLATE_DIRS = [
    'frontend/templates',
    'purchases/templates',
]

# URL هایی که نباید cafe_code داشته باشند
EXEMPT_URLS = [
    'login',
    'logout',
    'select_cafe',
    'home',
    'accounts_login_redirect',
    'system_health_check',
    'admin',
]

# Pattern برای پیدا کردن url tags
URL_PATTERN = re.compile(
    r"\{%\s*url\s+'([^']+)'([^%]*?)%\}",
    re.DOTALL
)

def should_add_cafe_code(url_name, existing_params):
    """بررسی اینکه آیا باید cafe_code اضافه شود"""
    # اگر url در لیست exempt است
    if url_name in EXEMPT_URLS:
        return False
    
    # اگر قبلاً cafe_code دارد
    if 'cafe_code' in existing_params:
        return False
    
    return True

def add_cafe_code_to_url(match):
    """اضافه کردن cafe_code به url tag"""
    url_name = match.group(1)
    params = match.group(2).strip()
    
    if not should_add_cafe_code(url_name, params):
        return match.group(0)
    
    # اضافه کردن cafe_code
    if params:
        # اگر پارامتر دارد
        new_params = f"{params} cafe_code=cafe_code"
    else:
        new_params = " cafe_code=cafe_code"
    
    return f"{{% url '{url_name}'{new_params} %}}"

def process_file(filepath):
    """پردازش یک فایل template"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # آپدیت URL ها
        new_content = URL_PATTERN.sub(add_cafe_code_to_url, content)
        
        if content != new_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
        return False
    except Exception as e:
        print(f"❌ خطا در پردازش {filepath}: {e}")
        return False

def main():
    base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    
    print("=" * 60)
    print("آپدیت URL های template ها")
    print("=" * 60)
    
    updated_count = 0
    total_count = 0
    
    for template_dir in TEMPLATE_DIRS:
        dir_path = base_dir / template_dir
        if not dir_path.exists():
            print(f"⚠️ دایرکتوری {template_dir} یافت نشد")
            continue
        
        for html_file in dir_path.glob('**/*.html'):
            total_count += 1
            if process_file(html_file):
                print(f"✅ آپدیت شد: {html_file.relative_to(base_dir)}")
                updated_count += 1
    
    print("=" * 60)
    print(f"کل فایل‌ها: {total_count}")
    print(f"آپدیت شده: {updated_count}")
    print("=" * 60)

if __name__ == '__main__':
    main()
