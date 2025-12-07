import pytest
from playwright.sync_api import Page, expect

def test_login_page_loads(page: Page, live_server):
    page.goto(f"{live_server.url}/login/")
    expect(page).to_have_title("Login - Syntel")
    expect(page.locator("h2")).to_contain_text("Login")

def test_login_functionality(page: Page, live_server):
    page.goto(f"{live_server.url}/login/")
    page.fill("input[name='username']", "manager")
    page.fill("input[name='password']", "password123")
    page.click("button[type='submit']")
    # Assuming successful login redirects to dashboard
    expect(page).to_have_url(f"{live_server.url}/dashboard/")
