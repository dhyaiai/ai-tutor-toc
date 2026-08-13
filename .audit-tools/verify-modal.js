/**
 * 验证 Modal / 聊天抽屉毛玻璃 + 上传弹窗内样式映射
 */
const puppeteer = require("puppeteer-core");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:5173";

async function main() {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith(BASE + "/api/v1")) {
      if (/\/auth\/refresh/.test(url)) req.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ access_token: "mock" }) });
      else req.continue();
    } else req.continue();
  });

  await page.goto(BASE + "/login", { waitUntil: "networkidle2" });
  await page.evaluate(() => {
    localStorage.setItem("access_token", "mock-token");
    localStorage.setItem("refresh_token", "mock-refresh");
    localStorage.setItem("user_id", "1");
    localStorage.setItem("username", "测试老师");
    localStorage.setItem("role", "teacher");
  });

  /* ---- 上传页：UploadModal 自动弹出 ---- */
  await page.goto(BASE + "/assignments/upload", { waitUntil: "networkidle2", timeout: 20000 });
  await new Promise((r) => setTimeout(r, 2500));

  const modalInfo = await page.evaluate(() => {
    const modal = document.querySelector(".ant-modal-content");
    const m = modal ? getComputedStyle(modal) : null;
    const drag = document.querySelector(".ant-upload-drag");
    const d = drag ? getComputedStyle(drag) : null;
    /* 拖拽区说明文字 */
    const tipEl = drag ? drag.querySelectorAll("p") : [];
    const tipColors = [...tipEl].map((p) => ({ text: p.textContent.slice(0, 14), color: getComputedStyle(p).color }));
    const title = document.querySelector(".ant-modal-title");
    return {
      modal: m ? { bg: m.backgroundColor, filter: m.backdropFilter, radius: m.borderRadius, shadow: m.boxShadow } : null,
      drag: d ? { bg: d.backgroundColor, border: d.borderColor, radius: d.borderRadius } : null,
      tipColors,
      titleColor: title ? getComputedStyle(title).color : null,
    };
  });
  console.log("MODAL:", JSON.stringify(modalInfo, null, 2));

  /* ---- 关闭弹窗后打开聊天抽屉 ---- */
  await page.evaluate(() => {
    const close = document.querySelector(".ant-modal-close");
    if (close) close.click();
  });
  await new Promise((r) => setTimeout(r, 800));
  const fab = await page.$(".ant-float-btn");
  if (fab) {
    await fab.click();
    await new Promise((r) => setTimeout(r, 1500));
    const drawerInfo = await page.evaluate(() => {
      const wrapper = document.querySelector(".ant-drawer-content-wrapper");
      const content = document.querySelector(".ant-drawer-content");
      const w = wrapper ? getComputedStyle(wrapper) : null;
      const c = content ? getComputedStyle(content) : null;
      const fab = document.querySelector(".ant-float-btn-body");
      const f = fab ? getComputedStyle(fab) : null;
      return {
        wrapperWidth: w ? w.width : null,
        drawer: c ? { bg: c.backgroundColor, filter: c.backdropFilter } : null,
        fab: f ? { bg: f.backgroundColor, filter: f.backdropFilter, shadow: f.boxShadow } : null,
      };
    });
    console.log("DRAWER:", JSON.stringify(drawerInfo, null, 2));
  } else {
    console.log("DRAWER: fab not found");
  }

  await browser.close();
}
main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
