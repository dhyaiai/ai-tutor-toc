/**
 * 作业管理页面浏览器级审计脚本（puppeteer-core + 系统 Chrome）
 *
 * 目的：在无后端 API 的情况下完整渲染作业管理页面，验证 soft-ui.css
 * 的 Soft-UI 视觉效果（白卡 + 柔和阴影 + 14px 圆角、侧边栏、毛玻璃弹窗）。
 *
 * 实现：
 * 1. 注入 localStorage JWT token（绕过 ProtectedRoute）
 * 2. route 拦截 /api/v1/** 返回 mock 数据（避免 401 登出 / 加载失败）
 * 3. 依次访问 5 个作业管理页面并截图
 * 4. 额外打开上传弹窗验证 Modal 毛玻璃
 */
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://localhost:5173";
const OUT = __dirname + "/shots";
const fs = require("fs");
fs.mkdirSync(OUT, { recursive: true });

/* ---------- mock 数据（与后端响应格式一致） ---------- */
const MOCK_ASSIGNMENTS = {
  items: [
    {
      id: 1, name: "五年级数学第一单元测试", grade: "五年级", subject: "数学",
      semester: "2025-2026 学年上学期", usage_month: "2026-03", layout_type: "single",
      status: "completed", total_score: 92, question_count: 18, created_at: "2026-03-12T10:30:00",
    },
    {
      id: 2, name: "六年级语文阅读理解练习", grade: "六年级", subject: "语文",
      semester: "2025-2026 学年上学期", usage_month: "2026-03", layout_type: "single",
      status: "completed", total_score: 87, question_count: 10, created_at: "2026-03-10T09:00:00",
    },
    {
      id: 3, name: "七年级英语 Unit 5 单词听写", grade: "七年级", subject: "英语",
      semester: "2025-2026 学年上学期", usage_month: "2026-02", layout_type: "multi",
      status: "grading", total_score: null, question_count: 24, created_at: "2026-03-08T15:20:00",
    },
    {
      id: 4, name: "三年级数学口算专项", grade: "三年级", subject: "数学",
      semester: "2025-2026 学年上学期", usage_month: "2026-02", layout_type: "single",
      status: "pending", total_score: null, question_count: 0, created_at: "2026-03-05T11:40:00",
    },
    {
      id: 5, name: "八年级物理力学单元卷", grade: "八年级", subject: "物理",
      semester: "2025-2026 学年上学期", usage_month: "2026-01", layout_type: "single",
      status: "failed", total_score: null, question_count: 12, created_at: "2026-03-01T08:00:00",
    },
  ],
  total: 5, page: 1, page_size: 10,
};

const MOCK_QUESTIONS = {
  items: [
    {
      id: 101, question_number: 1, question_type: "单选题", status: "completed",
      score: 5, full_score: 5, student_answer: "B", correct_answer: "B",
      knowledge_points: ["分数运算", "单位换算"], common_mistakes: [],
      analysis_detail: "正确，考查分数大小的比较，注意先统一单位。",
      confidence_score: 0.96, image_url: null,
      parent_id: null, sub_question_index: null,
      children: [
        {
          id: 1011, question_number: 1, question_type: "单选题", status: "completed",
          score: 3, full_score: 3, student_answer: "C", correct_answer: "A",
          knowledge_points: ["分数运算"], common_mistakes: ["混淆分子与分母"],
          analysis_detail: "错误，通分后分子比较方向反了。",
          confidence_score: 0.88, image_url: null, parent_id: 101, sub_question_index: 0,
        },
        {
          id: 1012, question_number: 1, question_type: "单选题", status: "completed",
          score: 2, full_score: 2, student_answer: "B", correct_answer: "B",
          knowledge_points: ["单位换算"], common_mistakes: [],
          analysis_detail: "正确。", confidence_score: 0.95, image_url: null,
          parent_id: 101, sub_question_index: 1,
        },
      ],
    },
    {
      id: 102, question_number: 2, question_type: "解答题", status: "completed",
      score: 7, full_score: 10, student_answer: "解：设 x 为甲数...", correct_answer: "解：设甲数为 x...",
      knowledge_points: ["方程", "应用题"], common_mistakes: ["未写设未知数步骤"],
      analysis_detail: "解题思路正确，计算过程有一步跳步，建议补全。",
      confidence_score: 0.82, image_url: null, parent_id: null, sub_question_index: null,
    },
  ],
  total: 2, page: 1, page_size: 10,
};

const MOCK_DETAIL = {
  id: 1, name: "五年级数学第一单元测试", grade: "五年级", subject: "数学",
  semester: "2025-2026 学年上学期", usage_month: "2026-03", layout_type: "single",
  status: "completed", total_score: 92, full_total: 100,
  ai_summary: "本卷整体掌握良好，分数运算与单位换算两个知识点完成度最高；方程应用题失分较多，建议专项巩固。",
  question_count: 18, file_url: null, created_at: "2026-03-12T10:30:00",
  questions: [
    { ...MOCK_QUESTIONS.items[0], image_url: null },
    { ...MOCK_QUESTIONS.items[1], image_url: null },
  ],
};

const MOCK_ERROR_QUESTIONS = {
  items: [
    {
      id: 201, question_number: 3, assignment_name: "五年级数学第一单元测试",
      assignment_id: 1, grade: "五年级", subject: "数学", semester: "2025-2026 学年上学期",
      question_type: "单选题", score: 2, full_score: 5, score_rate: 0.4,
      student_answer: "A", correct_answer: "C", knowledge_points: ["分数运算"],
      common_mistakes: ["通分错误"], analysis_detail: "通分时最小公倍数计算有误。",
      error: null,
      children: [
        {
          id: 2011, question_number: 3, sub_question_index: 0, question_type: "单选题",
          score: 2, full_score: 5, student_answer: "A", correct_answer: "C",
          knowledge_points: ["分数运算"],
        },
      ],
    },
  ],
  total: 1, page: 1, page_size: 10,
};

const MOCK_AI_QUESTIONS = {
  items: [
    {
      id: 301, question_text: "两个分数的分母相同，比较大小看什么？",
      answer: "看分子，分子大的分数大。", analysis: "同分母分数比较大小，分子大则分数大。",
      knowledge_point: "分数比较", question_type: "单选题", difficulty: "easy",
      score_rate: 0.8, created_at: "2026-03-11T14:00:00",
      children: [], latest_answer: null,
    },
    {
      id: 302, question_text: "阅读材料：小明有 3/4 块蛋糕，又得到 1/4 块，一共多少？",
      answer: "1 块。", analysis: "同分母分数相加，分母不变分子相加。",
      knowledge_point: "分数加法", question_type: "解答题", difficulty: "medium",
      score_rate: 0.6, created_at: "2026-03-10T16:30:00",
      question_context: "小明有 3/4 块蛋糕，又得到 1/4 块，一共多少？",
      children: [
        {
          id: 3021, sub_question_index: 0, question_type: "解答题",
          question_text: "小明有 3/4 块蛋糕，又得到 1/4 块，一共多少？",
          answer: "1 块。", knowledge_point: "分数加法",
          latest_answer: { score: 4, full_score: 5, ai_feedback: "步骤完整，注意写单位。" },
        },
      ],
      latest_answer: { score: 4, full_score: 5, ai_feedback: "步骤完整，注意写单位。" },
    },
  ],
  total: 2, page: 1, page_size: 10,
};

/* ---------- API mock 路由表 ---------- */
function mockResponse(request) {
  const url = request.url();
  const path = url.replace(BASE + "/api/v1", "");
  let body = null;
  if (/^\/assignments\?/.test(path) || path === "/assignments") body = MOCK_ASSIGNMENTS;
  else if (/^\/assignments\/[^/]+\?/.test(path) || /^\/assignments\/\d+$/.test(path)) body = MOCK_DETAIL;
  else if (/^\/error-questions/.test(path)) body = MOCK_ERROR_QUESTIONS;
  else if (/^\/ai-questions/.test(path)) body = MOCK_AI_QUESTIONS;
  else if (/^\/auth\/refresh/.test(path)) body = { access_token: "mock" };
  else if (/^\/analytics/.test(path)) body = {};
  else if (request.method() === "DELETE" || request.method() === "PATCH" || request.method() === "PUT") body = { ok: true };
  else body = null;

  if (body !== null) {
    request.respond({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  } else {
    request.continue();
  }
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  await page.setRequestInterception(true);
  page.on("request", (req) => {
    if (req.url().startsWith(BASE + "/api/v1")) mockResponse(req);
    else req.continue();
  });

  await page.goto(BASE + "/login", { waitUntil: "networkidle2" });
  await page.evaluate(() => {
    localStorage.setItem("access_token", "mock-token");
    localStorage.setItem("refresh_token", "mock-refresh");
    localStorage.setItem("user_id", "1");
    localStorage.setItem("username", "测试老师");
    localStorage.setItem("role", "teacher");
  });

  const shots = [
    ["records", "/assignments/records", 2500],
    ["upload", "/assignments/upload", 2500],
    ["detail", "/assignments/1", 3000],
    ["error-redo", "/assignments/error-redo", 3000],
    ["ai-challenge", "/assignments/ai-challenge", 3000],
  ];

  for (const [name, path, wait] of shots) {
    try {
      await page.goto(BASE + path, { waitUntil: "networkidle2", timeout: 20000 });
      await new Promise((r) => setTimeout(r, wait));
      await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
      console.log(`OK  ${name}`);
    } catch (e) {
      console.log(`ERR ${name}: ${e.message}`);
    }
  }

  /* 上传弹窗（UploadModal）毛玻璃验证：从上传页点击按钮打开 */
  try {
    await page.goto(BASE + "/assignments/upload", { waitUntil: "networkidle2", timeout: 20000 });
    await new Promise((r) => setTimeout(r, 2000));
    // 上传页自动弹出 Modal，直接截图
    await page.screenshot({ path: `${OUT}/upload-modal.png` });
    console.log("OK  upload-modal");
  } catch (e) {
    console.log(`ERR upload-modal: ${e.message}`);
  }

  await browser.close();
  console.log("DONE");
}

main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
