# -*- coding: utf-8 -*-
"""复现：普通话测评「生成朗读文本」无反应问题。真实账号登录 → 切换 tab → 点击 → 记录 console/网络。"""
import re
from playwright.sync_api import sync_playwright

PHONE = "18225831704"
PASSWORD = "123456"

events = []

def on_console(msg):
    events.append(f"[console:{msg.type}] {msg.text}")

def on_response(resp):
    u = resp.url
    if "mandarin" in u or ("/oral/" in u and "tts" not in u):
        events.append(f"[resp] {resp.status} {u}")

def on_request_failed(req):
    events.append(f"[req-fail] {req.url} :: {req.failure}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("console", on_console)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)

    try:
        # ── 登录 ──
        page.goto("http://localhost:5173/login", wait_until="networkidle")
        page.wait_for_timeout(800)
        events.append(f"[login-page] {page.url} body={'登录' in page.inner_text('body')}")

        phone_input = page.locator("input[placeholder*='手机'], input#phone, input:not([type='password'])").first
        # antd 登录表单：通常第一个输入框是手机号
        inputs = page.locator("input")
        events.append(f"[inputs] {inputs.count()}")
        if inputs.count() >= 1:
            inputs.nth(0).fill(PHONE)
        if inputs.count() >= 2:
            inputs.nth(1).fill(PASSWORD)
        page.wait_for_timeout(300)
        # 点击登录按钮
        login_btn = page.get_by_role("button", name="登 录")
        if login_btn.count() == 0:
            login_btn = page.get_by_role("button", name=re.compile("登.*录"))
        login_btn.click()
        page.wait_for_timeout(2500)
        events.append(f"[after-login-url] {page.url}")
        events.append(f"[body-has-退出] {'退出' in page.inner_text('body')}")

        # ── 进入听力与口语 ──
        page.goto("http://localhost:5173/oral", wait_until="networkidle")
        page.wait_for_timeout(1500)
        events.append(f"[oral-url] {page.url}")
        body = page.inner_text("body")
        events.append(f"[body-has-普通话测评] {'普通话测评' in body}")

        # ── 切换普通话测评 tab ──
        seg = page.locator(".soft-section-switcher")
        mt = seg.locator("text=普通话测评")
        if mt.count() > 0:
            mt.first.click()
            page.wait_for_timeout(800)
            events.append("[tab] 点击了普通话测评 tab")
        else:
            events.append("[tab] 未找到 .soft-section-switcher 中的普通话测评")

        # ── 找到并点击生成按钮 ──
        btn = page.get_by_role("button", name="生成朗读文本")
        n = btn.count()
        events.append(f"[btn-count] {n}")
        if n == 0:
            # 打印页面按钮列表辅助定位
            all_btns = [b for b in page.get_by_role("button").all_inner_texts() if b.strip()]
            events.append(f"[buttons] {all_btns[:20]}")
        else:
            b = btn.first
            events.append(f"[btn-state] disabled={b.is_disabled()} visible={b.is_visible()}")
            b.click()
            events.append("[clicked] 已点击")
            # 观察 12 秒
            for i in range(24):
                page.wait_for_timeout(500)
                body = page.inner_text("body")
                if "朗读文本已生成" in body or ("📖 朗读文本" in body):
                    events.append(f"[t={i*0.5:.1f}s] 已生成文本")
                    break
                if "生成朗读文本失败" in body or "生成文本失败" in body:
                    events.append(f"[t={i*0.5:.1f}s] 出现失败提示")
                    break
            events.append("[after-12s] " + page.inner_text("body")[:200].replace("\n", " | "))
            page.screenshot(path=r"d:\AICoding\AI_zhujiao\scripts_tmp_repro\shot.png")

        # 观察按钮 loading 状态变化
        events.append(f"[final-btn-loading] {btn.first.is_enabled() if n > 0 else 'n/a'}")
    except Exception as e:
        events.append(f"[EXCEPTION] {type(e).__name__}: {e}")
    finally:
        browser.close()

for e in events:
    print(e)
