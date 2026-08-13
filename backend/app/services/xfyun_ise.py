"""
讯飞语音评测（流式版 ISE）客户端

接口地址：wss://ise-api.xfyun.cn/v2/open-ise
鉴权方式：HMAC-SHA256 签名（host + date + request-line）
音频要求：采样率 16k、位长 16bit、单声道，aue=raw（原始 PCM）
协议流程：
    1. 参数帧（cmd=ssb, data.status=0）下发评测文本与音频参数
    2. 音频帧（cmd=auw, aus=1/2, data.status=1）分帧发送 PCM 数据
    3. 尾帧（cmd=auw, aus=4, data.status=2）通知音频结束
    4. 等待服务端返回 data.status=2 的最终结果（base64 编码的 XML）
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import struct
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse
from wsgiref.handlers import format_date_time

import websockets

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def extract_pcm_from_wav(data: bytes) -> bytes:
    """解析 WAV 文件并提取 PCM 数据。

    校验音频参数必须满足讯飞评测要求（16k / 16bit / 单声道 PCM），
    不满足时抛出 ValueError（调用方回退到 LLM 评测）。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("不是合法的 WAV 文件")
    pos = 12
    fmt_info: tuple | None = None
    pcm: bytes | None = None
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            fmt_info = struct.unpack("<HHIIHH", body[:16])
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + size + (size & 1)  # 块按 2 字节对齐
    if fmt_info is None or pcm is None:
        raise ValueError("WAV 文件缺少 fmt/data 块")
    audio_format, channels, sample_rate, _, _, bits = fmt_info
    if audio_format != 1 or channels != 1 or sample_rate != 16000 or bits != 16:
        raise ValueError(
            f"音频参数不符合评测要求（需 16k/16bit/单声道 PCM），"
            f"实际: fmt={audio_format} ch={channels} rate={sample_rate} bits={bits}"
        )
    return pcm


class XfyunIseClient:
    """讯飞流式语音评测 WebSocket 客户端"""

    def __init__(self):
        settings = get_settings()
        self.app_id = settings.XFYUN_APP_ID
        self.api_key = settings.XFYUN_API_KEY
        self.api_secret = settings.XFYUN_API_SECRET
        self.ise_url = settings.XFYUN_ISE_URL

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.api_key and self.api_secret)

    def _build_auth_url(self) -> str:
        """生成带 HMAC-SHA256 签名的 WebSocket 鉴权 URL"""
        parsed = urlparse(self.ise_url)
        host = parsed.netloc
        path = parsed.path or "/"
        date = format_date_time(time.time())  # RFC1123 格式 GMT 时间
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        # 保留原始 query（若 XFYUN_ISE_URL 已带参数），再安全追加鉴权参数，
        # 避免直接 f-string 拼接导致原始参数被丢弃/签名 URL 错误
        from urllib.parse import parse_qsl, urlunparse
        base_query = dict(parse_qsl(parsed.query))
        params = {**base_query, "authorization": authorization, "date": date, "host": host}
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(params), "")
        )

    async def evaluate_reading(
        self, pcm: bytes, text: str, category: str = "read_chapter",
    ) -> dict:
        """中文朗读评测（篇章朗读）。

        参数：
        - pcm: 16k/16bit/单声道原始 PCM 数据
        - text: 评测参考文本（朗读原文）
        - category: 题型，篇章朗读 read_chapter / 句子朗读 read_sentence

        返回解析后的评分字典（总分、声韵分、调型分、流畅度、完整度、错误字列表）。
        """
        if not pcm:
            raise ValueError("音频数据为空")
        if not self.configured:
            raise RuntimeError("讯飞评测鉴权信息未配置（XFYUN_APP_ID/API_KEY/API_SECRET）")

        # 限制音频时长：16k/16bit 单声道 = 32000 字节/秒，最长 5 分钟
        _MAX_PCM_BYTES = 32000 * 300
        if len(pcm) > _MAX_PCM_BYTES:
            raise ValueError("录音时长超过 5 分钟限制")

        url = self._build_auth_url()
        result_xml: bytes | None = None
        async with websockets.connect(url, max_size=None, open_timeout=15, close_timeout=5) as ws:
            # 1. 参数帧：下发评测文本（text 前需加 UTF-8 BOM）与音频参数
            await ws.send(json.dumps({
                "common": {"app_id": self.app_id},
                "business": {
                    "sub": "ise",
                    "ent": "cn_vip",           # 中文评测引擎
                    "category": category,
                    "cmd": "ssb",
                    "text": "\ufeff" + text,
                    "tte": "utf-8",
                    "ttp_skip": True,
                    "aue": "raw",
                    "auf": "audio/L16;rate=16000",
                    "rstcd": "utf8",
                },
                "data": {"status": 0, "data": ""},
            }))

            # 2. 音频帧：1280 字节/帧流式发送（稍作节流避免触发服务端限速）
            frame_size = 1280
            total_frames = (len(pcm) + frame_size - 1) // frame_size
            for i in range(total_frames):
                chunk = pcm[i * frame_size:(i + 1) * frame_size]
                await ws.send(json.dumps({
                    "business": {"cmd": "auw", "aus": 1 if i == 0 else 2, "aue": "raw"},
                    "data": {
                        "status": 1,
                        "data": base64.b64encode(chunk).decode("utf-8"),
                        "data_type": 1,
                        "encoding": "raw",
                    },
                }))
                await asyncio.sleep(0.008)

            # 3. 尾帧：通知音频发送完毕
            await ws.send(json.dumps({
                "business": {"cmd": "auw", "aus": 4, "aue": "raw"},
                "data": {"status": 2, "data": "", "data_type": 1, "encoding": "raw"},
            }))

            # 4. 等待最终评测结果（data.status == 2），设总体 deadline 防止死循环
            _result_deadline = time.monotonic() + 90
            while True:
                remaining = _result_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("讯飞评测结果等待超时（90秒）")
                raw = await asyncio.wait_for(ws.recv(), timeout=min(30, remaining))
                msg = json.loads(raw)
                if msg.get("code") != 0:
                    raise RuntimeError(
                        f"讯飞评测返回错误: code={msg.get('code')} "
                        f"message={msg.get('message')} sid={msg.get('sid')}"
                    )
                data = msg.get("data") or {}
                if data.get("status") == 2:
                    result_xml = base64.b64decode(data.get("data") or "")
                    break

        if not result_xml:
            raise RuntimeError("未收到讯飞评测结果")
        return self._parse_result_xml(result_xml)

    @staticmethod
    def _parse_result_xml(xml_bytes: bytes) -> dict:
        """解析评测结果 XML，提取总分、各维度分与朗读错误字。

        中文朗读结果节点属性：total_score（总分）、phone_score（声韵分）、
        tone_score（调型分）、fluency_score（流畅度）、integrity_score（完整度），
        均为百分制；is_rejected=true 表示检测到乱读。
        """
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="ignore"))
        overall = None
        for el in root.iter():
            if "total_score" in el.attrib:
                overall = el
                break
        if overall is None:
            raise RuntimeError("讯飞评测结果 XML 中未找到总分节点")

        def score(name: str) -> float:
            try:
                return round(float(overall.get(name) or 0), 1)
            except ValueError:
                return 0.0

        # 收集朗读有误的字：漏读/增读/回读（dp_message != 0）或声韵调错误（phone.perr_msg != 0）
        error_chars: list[str] = []
        for syll in root.iter("syll"):
            content = (syll.get("content") or "").strip()
            if not content or content in ("sil", "silv", "fil", "$"):
                continue
            dp = syll.get("dp_message") or "0"
            perr = "0"
            for phone in syll.iter("phone"):
                if (phone.get("perr_msg") or "0") != "0":
                    perr = phone.get("perr_msg") or "0"
                    break
            if (dp != "0" or perr != "0") and content not in error_chars:
                error_chars.append(content)
            if len(error_chars) >= 15:
                break

        return {
            "total_score": score("total_score"),
            "phone_score": score("phone_score"),
            "tone_score": score("tone_score"),
            "fluency_score": score("fluency_score"),
            "integrity_score": score("integrity_score"),
            "is_rejected": (overall.get("is_rejected") or "false").lower() == "true",
            "except_info": overall.get("except_info") or "",
            "error_chars": error_chars,
        }
