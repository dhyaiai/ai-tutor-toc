# -*- coding: utf-8 -*-
"""登录并输出 access token,供 curl 测后端接口。"""
import re
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:5173/login", wait_until="networkidle")
    page.wait_for_timeout(600)
    inputs = page.locator("input")
    inputs.nth(0).fill("18225831704")
    inputs.nth(1).fill("123456")
    page.get_by_role("button", name=re.compile("登.*录")).click()
    page.wait_for_timeout(2000)
    token = page.evaluate("localStorage.getItem('access_token')")
    print(token)
    browser.close()
