# -*- coding: utf-8 -*-
"""记录响应体与提示详情。"""
import re
from playwright.sync_api import sync_playwright

events = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    def on_response(resp):
        if "mandarin/generate-text" in resp.url:
            try:
                body = resp.text()[:400]
                events.append(f"[resp-body] status={resp.status} body={body}")
            except Exception as e:
                events.append(f"[resp-body] err {e}")

    page.on("response", on_response)
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
        btn.click()
        events.append("[clicked]")
        # 等到按钮 loading 结束
        for i in range(200):
            page.wait_for_timeout(500)
            cls = btn.get_attribute("class") or ""
            if "ant-btn-loading" not in cls:
                events.append(f"[t={i*0.5:.1f}s] loading 结束")
                break
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        # 提取提示信息(antd message)
        msgs = page.locator(".ant-message-notice").all_inner_texts()
        events.append(f"[messages] {msgs}")
        events.append(f"[body-关键] 朗读文本卡片={'📖 朗读文本' in body} 生成区={'生成朗读文本' in body}")
        # 找生成文本内容(若成功)
        if "📖 朗读文本" in body:
            card = page.get_by_text("📖 朗读文本").first
            events.append("[card] 存在生成文本卡片")
        page.screenshot(path=r"d:\AICoding\AI_zhujiao\scripts_tmp_repro\shot4.png")
    except Exception as e:
        events.append(f"[EXCEPTION] {type(e).__name__}: {e}")
    finally:
        browser.close()

for e in events:
    print(e)
