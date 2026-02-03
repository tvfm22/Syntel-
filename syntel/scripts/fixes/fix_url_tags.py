#!/usr/bin/env python
"""
اسکریپت برای اصلاح URL tags در Django templates
تبدیل {% url 'name' positional cafe_code=keyword %} به {% url 'name' cafe_code=keyword param=positional %}
"""
import re
import os
from pathlib import Path

# نقشه URL patterns به نام پارامترشان
URL_PARAM_MAP = {
    'purchase_list_edit_request': 'request_id',
    'purchase_list_confirm_request': 'request_id',
    'user_update': 'user_id',
    'user_delete': 'user_id',
    'user_reset_password': 'user_id',
    'product_update': 'product_id',
    'product_delete': 'product_id',
    'unit_update': 'unit_id',
    'unit_delete': 'unit_id',
    'category_update': 'category_id',
    'category_delete': 'category_id',
    'payment_type_update': 'payment_type_id',
    'payment_type_delete': 'payment_type_id',
    'purchase_order_detail': 'order_id',
    'purchase_order_update': 'order_id',
    'purchase_order_send': 'order_id',
    'purchase_order_confirm': 'order_id',
    'purchase_order_mark_in_transit': 'order_id',
    'purchase_order_deliver': 'order_id',
    'purchase_order_inspect': 'order_id',
    'purchase_order_complete': 'order_id',
    'purchase_order_cancel': 'order_id',
    'invoice_detail': 'invoice_id',
    'invoice_update': 'invoice_id',
    'invoice_send': 'invoice_id',
    'invoice_approve': 'invoice_id',
    'invoice_reject': 'invoice_id',
    'invoice_request_revision': 'invoice_id',
    'invoice_cancel': 'invoice_id',
    'invoice_delete': 'invoice_id',
    'invoice_pay': 'invoice_id',
    'payment_detail': 'payment_id',
    'payment_update': 'payment_id',
    'payment_delete': 'payment_id',
    'payment_location_update': 'method_id',
    'supplier_detail': 'supplier_id',
    'supplier_update': 'supplier_id',
    'supplier_delete': 'supplier_id',
    'supplier_activate': 'supplier_id',
    'supplier_deactivate': 'supplier_id',
    'supplier_evaluation_list': 'supplier_id',
    'supplier_evaluation_create': 'supplier_id',
    'requester_delete': 'requester_id',
    'invoice_issue_delete': 'issue_id',
    'purchase_request_detail': 'request_id',
    'update_purchase_request': 'request_id',
    'process_purchase_request': 'request_id',
    'purchase_order_create': 'request_id',
    'mark_item_for_stock_supply': 'item_id',
    'fulfill_stock_request': 'request_id',
    'stock_transactions': 'stock_id',
    'invoice_create': 'order_id',
    'payment_create_for_invoice': 'invoice_id',
    'notification_detail': 'notification_id',
    'delete_notification': 'notification_id',
}

def fix_url_tag(match):
    """اصلاح یک URL tag"""
    url_name = match.group(1)
    positional_arg = match.group(2)
    
    if url_name not in URL_PARAM_MAP:
        print(f"⚠️  Warning: Unknown URL name '{url_name}', skipping")
        return match.group(0)
    
    param_name = URL_PARAM_MAP[url_name]
    
    # تبدیل به keyword argument
    return f"{{% url '{url_name}' cafe_code=cafe_code {param_name}={positional_arg} %}}"

def process_file(filepath):
    """پردازش یک فایل template"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # الگوی جستجو: {% url 'name' something.id cafe_code=cafe_code %}
        pattern = r"{%\s*url\s+'(\w+)'\s+(\w+\.\w+)\s+cafe_code=cafe_code\s*%}"
        
        content = re.sub(pattern, fix_url_tag, content)
        
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception as e:
        print(f"❌ Error processing {filepath}: {e}")
        return False

def main():
    """تابع اصلی"""
    base_dir = Path(__file__).parent
    templates_dirs = [
        base_dir / 'frontend' / 'templates',
        base_dir / 'purchases' / 'templates',
    ]
    
    fixed_count = 0
    total_count = 0
    
    print("🔍 در حال جستجو و اصلاح URL tags...")
    
    for templates_dir in templates_dirs:
        if not templates_dir.exists():
            continue
            
        for html_file in templates_dir.rglob('*.html'):
            total_count += 1
            if process_file(html_file):
                fixed_count += 1
                print(f"✅ Fixed: {html_file.name}")
    
    print(f"\n📊 خلاصه:")
    print(f"   تعداد فایل‌های بررسی شده: {total_count}")
    print(f"   تعداد فایل‌های اصلاح شده: {fixed_count}")

if __name__ == '__main__':
    main()
