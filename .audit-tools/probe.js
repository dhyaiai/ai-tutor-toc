const puppeteer = require("puppeteer-core");
(async () => {
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.goto("http://localhost:5173/login", { waitUntil: "networkidle2" });
  const raw = await page.evaluate(() => {
    const el = document.createElement("div");
    el.style.color = "#999";
    el.style.background = "#fafafa";
    el.style.borderLeft = "3px solid #1890ff";
    document.body.appendChild(el);
    const out = { attr: el.getAttribute("style") };
    el.remove();
    return out;
  });
  console.log(JSON.stringify(raw));
  await browser.close();
})();
