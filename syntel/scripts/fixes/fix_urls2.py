#!/usr/bin/env python
"""
Fix missing spaces in Django URL template tags
"""

import os
import re
from pathlib import Path

def fix_url_spaces(content):
    """Fix missing space between URL name and first parameter"""
    # Pattern: {% url 'url_name'param ... %} -> {% url 'url_name' param ... %}
    pattern = r"(\{% url '[^']+')([a-zA-Z_][a-zA-Z0-9_\.]*)"
    
    def replacer(match):
        url_part = match.group(1)
        param = match.group(2)
        # Add space between url name and parameter
        return f"{url_part} {param}"
    
    return re.sub(pattern, replacer, content)

def process_directory(base_path, template_dir):
    dir_path = base_path / template_dir
    if not dir_path.exists():
        return 0
    
    count = 0
    for html_file in dir_path.glob('**/*.html'):
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = fix_url_spaces(content)
            
            if content != new_content:
                with open(html_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Fixed: {html_file.relative_to(base_path)}")
                count += 1
        except Exception as e:
            print(f"Error: {html_file}: {e}")
    
    return count

if __name__ == '__main__':
    base = Path(r'D:\Syntel\syntel')
    
    total = 0
    total += process_directory(base, 'frontend/templates')
    total += process_directory(base, 'purchases/templates')
    
    print(f"\nTotal files fixed: {total}")
