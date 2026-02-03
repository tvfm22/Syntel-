#!/usr/bin/env python
"""
اسکریپت اصلاح URL های template ها

این اسکریپت فاصله‌های گم شده بین پارامترها و cafe_code را اضافه می‌کند.
"""

import os
import re
from pathlib import Path

TEMPLATE_DIRS = [
    'frontend/templates',
    'purchases/templates',
]

def fix_missing_spaces(content):
    """اصلاح فاصله‌های گم شده قبل از cafe_code"""
    # Pattern: word/digit directly followed by cafe_code (no space)
    # Example: user.id cafe_code -> user.id cafe_code (add space before cafe_code)
    
    # Fix patterns like: 'name'param cafe_code -> 'name' param cafe_code
    content = re.sub(
        r"(\{% url '[^']+')([a-zA-Z_][a-zA-Z0-9_\.]*)\s+(cafe_code=cafe_code)",
        r"\1 \2 \3",
        content
    )
    
    # Fix patterns like: param.attr cafe_code -> param.attr cafe_code (ensure space)
    content = re.sub(
        r"([a-zA-Z_][a-zA-Z0-9_\.]+)(cafe_code=cafe_code)",
        r"\1 \2",
        content
    )
    
    return content

def process_file(filepath):
    """پردازش یک فایل template"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = fix_missing_spaces(content)
        
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
    print("اصلاح فاصله‌های URL ها")
    print("=" * 60)
    
    updated_count = 0
    
    for template_dir in TEMPLATE_DIRS:
        dir_path = base_dir / template_dir
        if not dir_path.exists():
            continue
        
        for html_file in dir_path.glob('**/*.html'):
            if process_file(html_file):
                print(f"✅ اصلاح شد: {html_file.relative_to(base_dir)}")
                updated_count += 1
    
    print("=" * 60)
    print(f"تعداد فایل‌های اصلاح شده: {updated_count}")
    print("=" * 60)

if __name__ == '__main__':
    main()
