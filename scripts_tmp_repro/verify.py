# -*- coding: utf-8 -*-
"""修复后验证：点击生成 → 应立刻出现进度提示 → 最终出现朗读文本卡片。"""
import re
from playwright.sync_api import sync_playwright

events = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("response", lambda r: events.append(f"[resp] {r.status} {r.url}") if "mandarin/generate-text" in r.url else None)

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
        btn.click()
        # 点击后 1 秒内应出现进度提示
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        events.append(f"[t=1s] 有进度提示={'正在生成朗读文本' in body} 按钮loading={'ant-btn-loading' in (btn.get_attribute('class') or '')}")

        # 等待最终结果（最长 90 秒）
        for i in range(180):
            page.wait_for_timeout(500)
            body = page.inner_text("body")
            if "📖 朗读文本" in body:
                events.append(f"[t={i*0.5:.1f}s] 成功!朗读文本卡片已出现")
                break
            if "生成失败" in body:
                events.append(f"[t={i*0.5:.1f}s] 失败提示出现")
                break
        page.screenshot(path=r"d:\AICoding\AI_zhujiao\scripts_tmp_repro\verify.png")
    except Exception as e:
        events.append(f"[EXCEPTION] {type(e).__name__}: {e}")
    finally:
        browser.close()

for e in events:
    print(e)
