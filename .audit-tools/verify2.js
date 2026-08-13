const puppeteer = require("puppeteer-core");
(async () => {
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith("http://localhost:5173/api/v1")) {
      if (/\/auth\/refresh/.test(url)) req.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ access_token: "mock" }) });
      else req.continue();
    } else req.continue();
  });
  await page.goto("http://localhost:5173/login", { waitUntil: "networkidle2" });
  await page.evaluate(() => {
    localStorage.setItem("access_token", "mock-token");
    localStorage.setItem("refresh_token", "mock-refresh");
    localStorage.setItem("user_id", "1");
    localStorage.setItem("username", "测试老师");
    localStorage.setItem("role", "teacher");
  });

  await page.goto("http://localhost:5173/assignments/upload", { waitUntil: "networkidle2", timeout: 20000 });
  await new Promise((r) => setTimeout(r, 2500));

  const r = await page.evaluate(() => {
    /* 1. 拖拽区提示文字（原 #999） */
    const drag = document.querySelector(".ant-upload-drag");
    const tips = drag ? [...drag.querySelectorAll("p")].map((p) => getComputedStyle(p).color) : [];
    /* 2. 外层 AssignmentLayout 背景（原内联 #f5f5f5） */
    const layoutOuter = document.querySelector(".ant-layout-sider")?.parentElement;
    const outerBg = layoutOuter ? getComputedStyle(layoutOuter).backgroundColor : null;
    /* 3. 气泡白字保护：模拟 React 序列化字面 rgb 形式 */
    const bubble = document.createElement("div");
    bubble.setAttribute("style", "background: rgb(22, 119, 255); color: rgb(255, 255, 255);");
    document.body.appendChild(bubble);
    const bubbleBg = getComputedStyle(bubble).backgroundColor;
    const bubbleColor = getComputedStyle(bubble).color;
    bubble.remove();
    /* 4. 文件图标模拟（color 形式，应映射深蓝） */
    const icon = document.createElement("div");
    icon.setAttribute("style", "color: rgb(22, 119, 255);");
    document.body.appendChild(icon);
    const iconColor = getComputedStyle(icon).color;
    icon.remove();
    /* 5. 子题边条模拟（border-left rgb(24,144,255) → 深蓝） */
    const sub = document.createElement("div");
    sub.setAttribute("style", "border-left: 3px solid rgb(24, 144, 255);");
    document.body.appendChild(sub);
    const subBorder = getComputedStyle(sub).borderLeftColor;
    sub.remove();
    /* 6. 浅灰底模拟（#fafafa → 淡蓝灰） */
    const box = document.createElement("div");
    box.setAttribute("style", "background: rgb(250, 250, 250);");
    document.body.appendChild(box);
    const boxBg = getComputedStyle(box).backgroundColor;
    box.remove();
    return { tips, outerBg, bubbleBg, bubbleColor, iconColor, subBorder, boxBg };
  });
  console.log(JSON.stringify(r, null, 2));
  await browser.close();
})();
