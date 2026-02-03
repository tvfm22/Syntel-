"""
Test Analytics APIs exactly as browser would call them
"""
import os
import django
import sys

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syntel.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()

def test_analytics_api():
    """Test API as browser would call it"""
    client = Client()
    
    # Login as admin
    admin_user = User.objects.filter(username='admin').first()
    if not admin_user:
        print("Error: Admin user not found")
        return
    
    client.force_login(admin_user)
    
    print("Testing Analytics API as browser would...")
    print(f"User: {admin_user.username}")
    
    # Test the exact URL browser would call
    test_urls = [
        '/local/api/analytics/departments/expenses/?period=weekly',
        '/local/api/analytics/departments/expenses/?period=monthly',
        '/local/api/analytics/departments/expenses/?period=quarterly',
    ]
    
    for url in test_urls:
        print(f"\nTesting: {url}")
        response = client.get(url)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            import json
            data = json.loads(response.content)
            print(f"Success! Response keys: {list(data.keys())}")
            
            if 'departments' in data:
                print(f"Departments count: {len(data['departments'])}")
                for dept in data['departments']:
                    current = dept.get('current_expense', 0)
                    name = dept.get('name', 'Unknown')
                    print(f"  - {name}: {current:,.0f} Toman")
        else:
            print(f"Error: {response.content.decode()[:200]}")

if __name__ == '__main__':
    test_analytics_api()
