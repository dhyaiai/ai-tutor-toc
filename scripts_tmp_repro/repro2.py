# -*- coding: utf-8 -*-
"""加强观测复现：监听全部请求、loading 状态、未捕获异常。"""
import re, json
from playwright.sync_api import sync_playwright

PHONE = "18225831704"
PASSWORD = "123456"
events = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # 捕获所有请求（含未到后端的）
    page.on("request", lambda req: events.append(f"[req] {req.method} {req.url}"))
    page.on("response", lambda resp: events.append(f"[resp] {resp.status} {resp.url}"))
    page.on("requestfailed", lambda req: events.append(f"[req-fail] {req.url} :: {req.failure}"))
    page.on("console", lambda m: events.append(f"[console:{m.type}] {m.text}") if m.type in ("error", "warning") else None)

    try:
        # ── 登录 ──
        page.goto("http://localhost:5173/login", wait_until="networkidle")
        page.wait_for_timeout(600)
        inputs = page.locator("input")
        inputs.nth(0).fill(PHONE)
        inputs.nth(1).fill(PASSWORD)
        page.get_by_role("button", name=re.compile("登.*录")).click()
        page.wait_for_timeout(2000)

        page.goto("http://localhost:5173/oral", wait_until="networkidle")
        page.wait_for_timeout(1200)

        # 切 tab
        seg = page.locator(".soft-section-switcher")
        seg.locator("text=普通话测评").first.click()
        page.wait_for_timeout(800)

        btn = page.get_by_role("button", name="生成朗读文本").first
        events.append(f"[btn] before-click loading-class={'ant-btn-loading' in (btn.get_attribute('class') or '')}")

        # 绑定全局错误/未处理 promise 捕获
        page.evaluate("""() => {
            window.__errs = [];
            window.addEventListener('error', (e) => window.__errs.push('error: ' + e.message));
            window.addEventListener('unhandledrejection', (e) => window.__errs.push('unhandledrejection: ' + String(e.reason)));
            window.__clickLog = [];
            // 原生点击测试：直接派发 click 看 React 是否响应
            const btnEl = [...document.querySelectorAll('button')].find(b => b.textContent.includes('生成朗读文本'));
            window.__btnEl = btnEl;
        }""")
        events.append("[js] 已安装错误监听")

        # 方式1：Playwright 真实点击
        btn.click()
        events.append("[clicked] Playwright 点击完成")
        page.wait_for_timeout(3000)
        events.append(f"[t=3s] btn-loading-class={'ant-btn-loading' in (btn.get_attribute('class') or '')}")
        errs = page.evaluate("window.__errs")
        events.append(f"[t=3s] errs={errs}")

        # 方式2：原生 DOM click（绕过 Playwright 层）
        page.evaluate("window.__btnEl && window.__btnEl.click()")
        events.append("[clicked2] 原生 DOM click")
        page.wait_for_timeout(3000)
        errs = page.evaluate("window.__errs")
        events.append(f"[t=6s] errs={errs}")

        body = page.inner_text("body")
        events.append("[t=6s] body-has-朗读文本=" + ("朗读文本已生成" in body or "📖 朗读文本" in body) + " body-has-error=" + ("失败" in body))

        # 检查按钮 onclick 属性是否绑定
        has_handler = page.evaluate("""() => {
            const el = window.__btnEl;
            return { handlers: el ? (el.onclick ? 'onclick-attached' : 'no-direct-onclick') : 'no-element', disabled: el ? el.disabled : 'n/a' };
        }""")
        events.append(f"[btn-introspect] {json.dumps(has_handler, ensure_ascii=False)}")

        page.screenshot(path=r"d:\AICoding\AI_zhujiao\scripts_tmp_repro\shot2.png")
    except Exception as e:
        events.append(f"[EXCEPTION] {type(e).__name__}: {e}")
    finally:
        browser.close()

for e in events:
    print(e)
