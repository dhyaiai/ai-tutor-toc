# -*- coding: utf-8 -*-
"""干净复现：点击生成 → 逐秒记录按钮 loading / 页面卡片 / message,最长 40 秒。"""
import re
from playwright.sync_api import sync_playwright

events = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("request", lambda r: events.append(f"[req] {r.method} {r.url}") if "mandarin" in r.url else None)
    page.on("response", lambda r: events.append(f"[resp] {r.status} {r.url}") if "mandarin" in r.url else None)
    page.on("requestfailed", lambda r: events.append(f"[req-fail] {r.url} {r.failure}") if "mandarin" in r.url else None)

    try:
        page.goto("http://localhost:5173/login", wait_until="networkidle")
        page.wait_for_timeout(600)
        inputs = page.locator("input")
        inputs.nth(0).fill("18225831704")
        inputs.nth(1).fill("123456")
        page.get_by_role("button", name=re.compile("登.*录")).click()
        page.wait_for_timeout(2000)

        page.goto("http://localhost:5173/oral", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.locator(".soft-section-switcher").locator("text=普通话测评").first.click()
        page.wait_for_timeout(800)

        btn = page.get_by_role("button", name="生成朗读文本").first
        events.append(f"[start] btn visible={btn.is_visible()} disabled={btn.is_disabled()}")
        btn.click()
        events.append("[clicked]")
        for i in range(80):  # 40 秒
            page.wait_for_timeout(500)
            cls = btn.get_attribute("class") or ""
            loading = "ant-btn-loading" in cls
            body = page.inner_text("body")
            has_card = "📖 朗读文本" in body
            has_msg = "已生成" in body or "失败" in body
            if i % 4 == 0 or has_card or has_msg:
                events.append(f"[t={i*0.5:.1f}s] loading={loading} card={has_card} msg={has_msg}")
            if has_card:
                events.append(f"[t={i*0.5:.1f}s] 成功!生成文本出现在页面")
                break
            if has_msg:
                events.append(f"[t={i*0.5:.1f}s] 出现提示(成功或失败)")
                break
        page.screenshot(path=r"d:\AICoding\AI_zhujiao\scripts_tmp_repro\shot3.png")
    except Exception as e:
        events.append(f"[EXCEPTION] {type(e).__name__}: {e}")
    finally:
        browser.close()

for e in events:
    print(e)
