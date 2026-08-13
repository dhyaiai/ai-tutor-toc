"""验证 explain_full 新失败处理:真实 API 回归 + mock 字段空场景。"""
import asyncio
import base64
import sys
import time
import types

sys.path.insert(0, r"d:\AICoding\AI_zhujiao\backend")

GOOD_JSON = '{"knowledge_points":["加法"],"explanation":"这是一段完整讲解内容，覆盖考点与步骤。","thinking_question":"请思考一个变式问题？"}'
EMPTY_JSON = '{"knowledge_points":[],"explanation":"","thinking_question":""}'

async def load_image():
    import app.db.base  # noqa: F401
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.question import Question
    from app.services.file_upload import MIME_MAP, StorageService

    async with async_session_factory() as db:
        q = (await db.execute(
            select(Question).where(Question.image_url.isnot(None)).limit(1)
        )).scalar()
        image_url = q.image_url
    storage = StorageService()
    img_bytes = await storage.get_file_bytes(image_url)
    ext = image_url.rsplit(".", 1)[-1].lower() if "." in image_url else "png"
    mime = MIME_MAP.get(ext, "image/png")
    return f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"


class _FakeResp:
    def __init__(self, content):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


class _FakeCompletions:
    def __init__(self, impl):
        self._impl = impl

    async def create(self, **kwargs):
        return _FakeResp(self._impl(kwargs))


class _FakeClient:
    def __init__(self, impl):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(impl))


async def main():
    import app.services.explain_service as es
    from app.services.explain_service import ExplainService

    data_url = await load_image()

    # 场景A:真实 API 多模态(正常)
    t0 = time.time()
    res = await ExplainService().explain_full(
        exercise_content="请讲解这道题", subject="语文", images=[data_url])
    print(f"[A 真实多模态] {time.time()-t0:.1f}s OK explanation={len(res['explanation'])}字")

    # 场景B:VISION key 错误 → 降级纯文本
    from app.core.config import get_settings
    settings = get_settings()
    orig = settings.VISION_API_KEY
    settings.VISION_API_KEY = "sk-invalid-for-test"
    try:
        t0 = time.time()
        res = await ExplainService().explain_full(
            exercise_content="请讲解 2+2=?", subject="数学", images=[data_url])
        print(f"[B VISION失败→降级] {time.time()-t0:.1f}s OK explanation={len(res['explanation'])}字")
    finally:
        settings.VISION_API_KEY = orig

    # 场景D:VISION 返回字段空 JSON → 应降级纯文本(deepseek)成功
    def vision_empty_impl(kwargs):
        return EMPTY_JSON

    def deepseek_good_impl(kwargs):
        return GOOD_JSON

    es.AsyncOpenAI = lambda **kw: _FakeClient(
        vision_empty_impl if "aliyuncs" in kw.get("base_url", "") else deepseek_good_impl)
    t0 = time.time()
    res = await ExplainService().explain_full(
        exercise_content="请讲解", subject="语文", images=["data:image/png;base64,x"])
    print(f"[D VISION字段空→降级] {time.time()-t0:.1f}s OK explanation={len(res['explanation'])}字")

    # 场景E:所有路径都返回字段空 → 抛 ValueError(不再静默)
    es.AsyncOpenAI = lambda **kw: _FakeClient(lambda _k: EMPTY_JSON)
    try:
        await ExplainService().explain_full(
            exercise_content="请讲解", subject="语文", images=["data:image/png;base64,x"])
        print("[E 全部字段空] 未抛异常(不符合预期!)")
    except ValueError as e:
        print(f"[E 全部字段空] 正确抛出 ValueError: {e}")


asyncio.run(main())
