/**
 * Soft-UI 样式生效验证脚本
 * 在浏览器中读取计算样式 + style 属性字面值，逐条核对设计规则：
 * 1. 页面背景 #f7f8fa
 * 2. 卡片纯白底 + 14px 圆角 + 柔和阴影
 * 3. 顶部导航深蓝半透明 + backdrop-filter blur
 * 4. 主按钮深蓝实底 #1a56db
 * 5. 侧边栏菜单选中项主色浅染
 * 6. Modal 毛玻璃（rgba 白 + backdrop-filter）
 * 7. React 内联样式字面形式（#1677ff vs rgb）确认映射命中
 */
const puppeteer = require("puppeteer-core");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:5173";
const fs = require("fs");

const MOCK_ASSIGNMENTS = {
  items: [
    { id: 1, name: "五年级数学第一单元测试", grade: "五年级", subject: "数学",
      semester: "2025-2026 学年上学期", usage_month: "2026-03", layout_type: "single",
      status: "completed", total_score: 92, question_count: 18, created_at: "2026-03-12T10:30:00" },
  ],
  total: 1, page: 1, page_size: 10,
};

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
      let body = null;
      if (/\/assignments\?/.test(url)) body = MOCK_ASSIGNMENTS;
      else if (/\/auth\/refresh/.test(url)) body = { access_token: "mock" };
      if (body !== null) req.respond({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
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

  await page.goto(BASE + "/assignments/records", { waitUntil: "networkidle2", timeout: 20000 });
  await new Promise((r) => setTimeout(r, 2500));

  const results = await page.evaluate(() => {
    const cs = (el) => {
      if (!el) return null;
      const s = getComputedStyle(el);
      return {
        bg: s.backgroundColor,
        radius: s.borderRadius,
        shadow: s.boxShadow,
        color: s.color,
        filter: s.backdropFilter || s.webkitBackdropFilter,
      };
    };
    const card = document.querySelector(".ant-card");
    const header = document.querySelector(".ant-layout-header");
    const content = document.querySelector(".ant-layout-content");
    const body = document.body;
    const menuSelected = document.querySelector(".ant-layout-sider .ant-menu-item-selected");
    const primaryBtn = document.querySelector(".ant-btn-primary");
    const sider = document.querySelector(".ant-layout-sider");
    const tableHead = document.querySelector(".ant-table-thead th");

    /* 内联样式字面形式采样：找所有含 #1677ff / rgb(22,119,255) 字面的元素 */
    const inlineSamples = [];
    document.querySelectorAll("[style]").forEach((el) => {
      const raw = el.getAttribute("style") || "";
      if (raw.includes("#1677ff") || raw.includes("rgb(22, 119, 255)")) {
        inlineSamples.push(raw.slice(0, 160));
      }
    });

    /* 计算色板：页面内实际出现的背景/文字主色 */
    const colorCounts = {};
    document.querySelectorAll("*").forEach((el) => {
      const s = getComputedStyle(el);
      const key = s.backgroundColor + "|" + s.color;
      colorCounts[key] = (colorCounts[key] || 0) + 1;
    });
    const topColors = Object.entries(colorCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12)
      .map(([k, n]) => ({ color: k, count: n }));

    return {
      pageBg: cs(body),
      contentBg: cs(content),
      card: cs(card),
      header: cs(header),
      menuSelected: cs(menuSelected),
      primaryBtn: cs(primaryBtn),
      sider: cs(sider),
      tableHead: cs(tableHead),
      inlineSamplesCount: inlineSamples.length,
      inlineSamples: inlineSamples.slice(0, 8),
      topColors,
    };
  });

  console.log(JSON.stringify(results, null, 2));
  await browser.close();
}

main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
