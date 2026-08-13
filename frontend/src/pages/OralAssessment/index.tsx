/**
 * 听力与口语页面
 *
 * 3 个子模块（大号分段切换器切换，与作文批改的语文/英语切换一致）：
 * - 英语听力：AI 生成听力试卷 → 在线作答 → 自动批改
 * - 单词听写：选择词库范围 → 播放听写 → 提交批改
 * - 普通话测评：输入/朗读文段 → AI 评分反馈
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Segmented, Card, Form, Select, InputNumber, Button, Input, Typography,
  List, Tag, Spin, message, Result, Empty, Radio, Divider, Space, Progress, Modal, Alert,
  Popconfirm, Upload, Pagination, Dropdown,
} from "antd";
import {
  SoundOutlined, EditOutlined, CustomerServiceOutlined,
  PlayCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, PauseCircleOutlined,
  CaretRightOutlined, LoadingOutlined, InboxOutlined, DeleteOutlined,
  EyeOutlined, MoreOutlined, ReloadOutlined,
} from "@ant-design/icons";
import {
  oralService,
  type ListeningQuestion,
  type DictationWord,
  type ListeningResult,
  type ListeningDetail,
  type DictationResult,
  type DictationDetailItem,
  type MandarinResult,
  type OralRecord,
  type OralRecordDetail,
} from "../../services/oralService";
import { formatDate, parseOralScore } from "../../utils/helpers";
import { getTtsVoice } from "../../utils/ttsVoice";
import { authedFetch } from "../../utils/authedFetch";
import PlaybackRateControl, { usePlaybackRate } from "../../components/PlaybackRateControl";
import AuthedAudio from "../../components/AuthedAudio";
import "./index.css";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

/**
 * 带登录凭证的 TTS 请求（/oral/tts 等端点要求登录）。
 * 语音播放必须先 fetch 成 Blob 再交给 Audio 元素，无法让 Audio 直接带
 * Authorization 头；authedFetch 统一附加 Bearer token 并处理 401 自动刷新。
 */
function ttsFetch(url: string, signal?: AbortSignal): Promise<Response> {
  return authedFetch(url, { signal });
}

/** 清理听力原文：去除说话人标签，按句子拆分 */
function cleanTranscript(text: string): string[] {
  const cleaned = (text || "")
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(M|W|Man|Woman|Speaker\s*\d|[A-Z][a-z]+)\s*[:：]\s*/i, ""))
    .filter(Boolean)
    .join(". ");
  if (!cleaned.trim()) return [];
  const sentences = cleaned.split(/(?<=[.!?])\s+/).filter((s) => s.trim().length > 0);
  return sentences.length > 0 ? sentences : [cleaned];
}

/**
 * 听力原文播放 Hook
 *
 * 使用后端 Edge TTS 生成真实音频文件（Audio 元素播放）：
 * - 不依赖浏览器 SpeechSynthesis API（中文 Windows 常缺英文语音）
 * - 神经网络语音，发音自然清晰
 * - Audio 元素原生支持暂停/继续/进度追踪
 * - 长文本一次性发送后端合成，无需拆分
 *
 * @param storageKey 速度持久化 key（听力 "listening"，单词听写 "dictation"），用户修改后保存，后续播放均沿用
 * @param defaultRate 未保存过时的默认播放速度（听力 1.0，单词听写 0.8），可在 0.75-1.5 间调整
 */
function useTranscriptPlayer(storageKey: string, defaultRate = 1) {
  const [playState, setPlayState] = useState<"idle" | "loading" | "playing" | "paused">("idle");
  const [progress, setProgress] = useState(0);
  // 速度状态/持久化由共享 hook 管理，播放器在此只补充 audio 元素联动
  const { playbackRate, setPlaybackRate: persistRate, playbackRateRef } = usePlaybackRate(storageKey, defaultRate);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const stoppedRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  /** 调整播放速度（播放中即时生效，并持久化保存，后续播放沿用） */
  const setPlaybackRate = (rate: number) => {
    persistRate(rate);
    if (audioRef.current) audioRef.current.playbackRate = rate;
  };

  /* 组件卸载时清理 audio 元素和 blob URL */
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (audioRef.current) {
        try { audioRef.current.pause(); } catch { /* ignore */ }
        audioRef.current.src = "";
        audioRef.current = null;
      }
      if (blobUrlRef.current) { URL.revokeObjectURL(blobUrlRef.current); blobUrlRef.current = null; }
      if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    };
  }, []);

  const clearTimer = () => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };

  /** 用定时器轮询 audio.currentTime 更新进度 */
  const startProgressPolling = () => {
    clearTimer();
    timerRef.current = window.setInterval(() => {
      const audio = audioRef.current;
      if (audio && audio.duration && isFinite(audio.duration)) {
        const pct = Math.round((audio.currentTime / audio.duration) * 100);
        setProgress(Math.min(99, pct));
      }
    }, 150);
  };

  /** 从头播放文本（使用后端 Edge TTS → fetch Blob → Audio 播放）
   *  voice 可选：default(英语)/mixed(中文，可读中英混合的单词听写播报)，
   *  实际男声/女声按助教设置的音色偏好解析（getTtsVoice）
   *  播放速度由 playbackRate 控制（Audio.playbackRate，客户端变速） */
  const speak = (text: string, voice = "default") => {
    // 停止之前的播放（先移除事件回调，防止清理时触发 error 弹窗）
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      try { audioRef.current.pause(); } catch { /* ignore */ }
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (blobUrlRef.current) { URL.revokeObjectURL(blobUrlRef.current); blobUrlRef.current = null; }
    if (abortRef.current) { abortRef.current.abort(); }
    clearTimer();
    stoppedRef.current = false;

    // 清理文本
    const cleaned = cleanTranscript(text).join(". ");
    if (!cleaned.trim()) {
      message.warning("没有可播放的文本内容");
      return;
    }

    setProgress(0);
    setPlayState("loading");

    // 服务端按原速合成，变速统一由客户端 playbackRate 完成（避免叠加变速）
    const controller = new AbortController();
    abortRef.current = controller;

    // 先按助教设置的音色偏好解析语音（男声/女声），再用 fetch 下载音频 Blob（可检测 HTTP 错误、支持 AbortController）
    getTtsVoice(voice)
      .then((resolvedVoice) => {
        // 用户已停止则中断，避免泄漏 Blob URL
        if (stoppedRef.current) throw new Error("stopped");
        const fetchUrl = `/api/v1/oral/tts?text=${encodeURIComponent(cleaned)}&rate=${encodeURIComponent("+0%")}&voice=${encodeURIComponent(resolvedVoice)}`;
        return ttsFetch(fetchUrl, controller.signal);
      })
      .then(async (response) => {
        if (!response.ok) throw new Error(`服务端错误 (${response.status})`);
        const blob = await response.blob();
        if (blob.size === 0) throw new Error("服务端返回空音频");
        return blob;
      })
      .then((blob) => {
        if (stoppedRef.current) return;
        // 创建 Blob URL 供 Audio 元素播放
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;

        const audio = new Audio(blobUrl);
        audio.playbackRate = playbackRateRef.current;

        audio.onended = () => {
          if (stoppedRef.current) return;
          setPlayState("idle"); setProgress(100); clearTimer();
        };

        audio.onerror = () => {
          console.warn("[TTS] blob playback error");
          if (stoppedRef.current) return;
          setPlayState("idle"); clearTimer();
          message.error("音频播放失败，请重试");
        };

        audioRef.current = audio;

        return audio.play().then(() => {
          if (stoppedRef.current) return;
          setPlayState("playing");
          startProgressPolling();
        });
      })
      .catch((err) => {
        if (stoppedRef.current) return;
        if (err.name === "AbortError") return; // 用户主动停止
        if (err.message === "stopped") return; // 用户快速停止，中断 promise 链
        console.warn("[TTS] 后端 TTS 不可用 (%s)，回退浏览器 TTS", err.message);
        message.warning("语音服务暂不可用，请确认后端服务已重启。正在尝试浏览器语音...");
        // 后端 TTS 失败 → 回退浏览器 SpeechSynthesis（状态由回退控制，不闪 idle）
        speakWithBrowserTTS(
          cleaned, playbackRateRef.current,
          /* onDone */  () => { setPlayState("idle"); setProgress(100); clearTimer(); },
          /* onStart */  () => { setPlayState("playing"); },
          /* onProgress */ (pct) => setProgress(pct),
        );
      });
  };

  /** 暂停播放 */
  const pause = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      setPlayState("paused");
      clearTimer();
    }
  };

  /** 继续播放 */
  const resume = () => {
    if (audioRef.current) {
      audioRef.current.play().catch(() => {});
      setPlayState("playing");
      startProgressPolling();
    }
  };

  /** 完全停止 */
  const stop = () => {
    stoppedRef.current = true;
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    if (audioRef.current) {
      try { audioRef.current.pause(); } catch { /* ignore */ }
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (blobUrlRef.current) { URL.revokeObjectURL(blobUrlRef.current); blobUrlRef.current = null; }
    clearTimer();
    setPlayState("idle");
    setProgress(0);
  };

  /**
   * 播放对话文本（M→男声 W→女声）。
   * 调用后端 /oral/tts-dialogue 接口，自动分男女声合成。
   * 失败时回退浏览器 TTS。
   */
  const speakDialogue = (text: string) => {
    // 停止之前的播放（先移除事件回调，防止清理时触发 error 弹窗）
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      try { audioRef.current.pause(); } catch { /* ignore */ }
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (blobUrlRef.current) { URL.revokeObjectURL(blobUrlRef.current); blobUrlRef.current = null; }
    if (abortRef.current) { abortRef.current.abort(); }
    clearTimer();
    stoppedRef.current = false;

    if (!text.trim()) {
      message.warning("没有可播放的对话内容");
      return;
    }

    setProgress(0);
    setPlayState("loading");

    const fetchUrl = `/api/v1/oral/tts-dialogue?text=${encodeURIComponent(text)}&rate=${encodeURIComponent("+0%")}`;
    const controller = new AbortController();
    abortRef.current = controller;

    ttsFetch(fetchUrl, controller.signal)
      .then(async (response) => {
        if (!response.ok) throw new Error(`服务端错误 (${response.status})`);
        const blob = await response.blob();
        if (blob.size === 0) throw new Error("服务端返回空音频");
        return blob;
      })
      .then((blob) => {
        if (stoppedRef.current) return;
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;

        const audio = new Audio(blobUrl);
        audio.playbackRate = playbackRateRef.current;

        audio.onended = () => {
          if (stoppedRef.current) return;
          setPlayState("idle"); setProgress(100); clearTimer();
        };

        audio.onerror = () => {
          console.warn("[TTS-Dialogue] blob playback error");
          if (stoppedRef.current) return;
          setPlayState("idle"); clearTimer();
          message.error("音频播放失败，请重试");
        };

        audioRef.current = audio;

        return audio.play().then(() => {
          if (stoppedRef.current) return;
          setPlayState("playing");
          startProgressPolling();
        });
      })
      .catch((err) => {
        if (stoppedRef.current) return;
        if (err.name === "AbortError") return;
        console.warn("[TTS-Dialogue] 后端对话 TTS 不可用 (%s)，回退浏览器 TTS", err.message);
        message.warning("语音服务暂不可用，请确认后端服务已重启。正在尝试浏览器语音...");
        speakWithBrowserTTS(
          text, playbackRateRef.current,
          () => { setPlayState("idle"); setProgress(100); clearTimer(); },
          () => { setPlayState("playing"); },
          (pct) => setProgress(pct),
        );
      });
  };

  /**
   * 播放已缓存的 Blob URL（跳过 fetch 步骤）。
   * 用于预缓存场景，直接播放本地 Blob，加载速度极快。
   */
  const playBlob = (blobUrl: string) => {
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      try { audioRef.current.pause(); } catch { /* ignore */ }
      audioRef.current.src = "";
      audioRef.current = null;
    }
    clearTimer();
    stoppedRef.current = false;

    setProgress(0);
    setPlayState("loading");

    const audio = new Audio(blobUrl);
    audio.playbackRate = playbackRateRef.current;

    audio.onended = () => {
      if (stoppedRef.current) return;
      setPlayState("idle"); setProgress(100); clearTimer();
    };

    audio.onerror = () => {
      if (stoppedRef.current) return;
      setPlayState("idle"); clearTimer();
      message.error("音频播放失败，请重试");
    };

    audioRef.current = audio;

    audio.play().then(() => {
      if (stoppedRef.current) return;
      setPlayState("playing");
      startProgressPolling();
    }).catch((err) => {
      if (stoppedRef.current) return;
      console.warn("[TTS] blob playback error: %s", err.message);
      setPlayState("idle"); clearTimer();
      message.error("音频播放失败，请重试");
    });
  };

  return { playState, progress, playbackRate, setPlaybackRate, speak, speakDialogue, playBlob, pause, resume, stop };
}

/**
 * 浏览器 SpeechSynthesis 回退方案
 * 当后端 TTS 不可用时使用，逐句播放
 */
function speakWithBrowserTTS(
  text: string,
  rate: number,
  onDone: () => void,
  onStart: () => void,
  onProgress: (pct: number) => void,
) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
  if (!synth) { message.warning("当前环境不支持语音播放"); onDone(); return; }

  // 重置 synth 状态
  try { synth.resume(); } catch { /* ignore */ }
  synth.cancel();
  try { synth.resume(); } catch { /* ignore */ }

  const sentences = text.split(/(?<=[.!?])\s+/).filter((s) => s.trim().length > 0);
  if (sentences.length === 0) { onDone(); return; }

  let idx = 0;
  const total = sentences.length;
  onStart(); // 通知调用方已经开始播放
  onProgress(0);

  const speakNext = () => {
    if (idx >= total) { onDone(); return; }
    const s = sentences[idx];
    const u = new SpeechSynthesisUtterance(s);
    u.lang = "en-US";
    u.rate = rate;
    const voices = synth.getVoices();
    const en = voices.find((v) => v.lang?.toLowerCase().startsWith("en"));
    if (en) u.voice = en;

    u.onend = () => { idx++; onProgress(Math.round((idx / total) * 100)); speakNext(); };
    u.onerror = () => { idx++; speakNext(); };

    synth.speak(u);
  };

  speakNext();
}

/**
 * 播放英文单词发音（单词级别）。
 * 优先有道词典真人发音接口，失败回退浏览器 TTS。
 */
function playEnglishAudio(text: string, rate = 1) {
  const word = (text || "").trim();
  if (!word) return;
  try {
    const url = `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(word)}&type=2`;
    const audio = new Audio(url);
    audio.playbackRate = rate;
    audio.play().catch(() => speakWord(word, rate));
  } catch {
    speakWord(word, rate);
  }
}

/** 浏览器 TTS 单词发音（有道接口回退方案） */
function speakWord(text: string, rate: number) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
  if (!synth) { message.warning("当前环境不支持语音播放"); return; }
  // 重置 synth 状态避免 Chrome 静音 bug
  try { synth.resume(); } catch { /* ignore */ }
  synth.cancel();
  try { synth.resume(); } catch { /* ignore */ }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = rate;
  const voices = synth.getVoices();
  const en = voices.find((v) => v.lang?.toLowerCase().startsWith("en"));
  if (en) u.voice = en;
  setTimeout(() => synth.speak(u), 100);
}

/**
 * 将录音 Blob 转为讯飞语音评测要求的 16k/16bit/单声道 WAV。
 *
 * 通过 WebAudio 解码浏览器录音格式（webm/opus、mp4 等），
 * 再用 OfflineAudioContext 重采样到 16000Hz 单声道，最后写入 WAV 头。
 */
async function blobToWav16k(blob: Blob): Promise<Blob> {
  const arrayBuffer = await blob.arrayBuffer();
  const ctx = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await ctx.decodeAudioData(arrayBuffer);
  } finally {
    void ctx.close();
  }
  const targetRate = 16000;
  const length = Math.max(1, Math.ceil(decoded.duration * targetRate));
  const offline = new OfflineAudioContext(1, length, targetRate);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();
  const samples = rendered.getChannelData(0);

  // 写 WAV 头（44 字节）+ 16bit PCM 数据
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);          // fmt 块长度
  view.setUint16(20, 1, true);           // PCM 编码
  view.setUint16(22, 1, true);           // 单声道
  view.setUint32(24, targetRate, true);  // 采样率
  view.setUint32(28, targetRate * 2, true);  // 字节率
  view.setUint16(32, 2, true);           // 块对齐
  view.setUint16(34, 16, true);          // 位深
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/**
 * 音频录制 Hook
 *
 * 使用浏览器 MediaRecorder API 录制音频，返回 Blob 和播放 URL。
 */
function useAudioRecorder() {
  const [recording, setRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [recordingTime, setRecordingTime] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  // 追踪最新的 audioUrl，避免卸载时闭包捕获旧值导致 Blob URL 泄漏
  const audioUrlRef = useRef<string | null>(null);

  // 清理定时器
  const clearTimer = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  }, []);

  /** 开始录音 */
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // 优先使用 webm 格式
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "audio/mp4";
      const recorder = new MediaRecorder(stream, { mimeType });
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const ext = mimeType.includes("webm") ? "webm" : mimeType.includes("mp4") ? "m4a" : "webm";
        const blob = new Blob(chunksRef.current, { type: mimeType });
        setAudioBlob(blob);
        // 清理旧 URL（使用 ref 追踪最新值，避免闭包捕获旧值导致泄漏）
        if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
        const newUrl = URL.createObjectURL(blob);
        audioUrlRef.current = newUrl;
        setAudioUrl(newUrl);
        // 停止所有音轨
        stream.getTracks().forEach((t) => t.stop());
        clearTimer();
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
      setRecordingTime(0);
      // 计时器
      timerRef.current = window.setInterval(() => {
        setRecordingTime((t) => t + 1);
      }, 1000);
    } catch {
      message.error("无法访问麦克风，请检查浏览器权限设置");
    }
  }, [audioUrl, clearTimer]);

  /** 停止录音 */
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
      setRecording(false);
    }
    clearTimer();
  }, [clearTimer]);

  /** 重置录音状态 */
  const reset = useCallback(() => {
    clearTimer();
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
    setRecordingTime(0);
    if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = null; }
    setAudioUrl(null);
    setAudioBlob(null);
  }, [clearTimer]);

  // 组件卸载时清理（使用 ref 确保拿到最新值）
  useEffect(() => {
    return () => {
      clearTimer();
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
      if (audioUrlRef.current) { URL.revokeObjectURL(audioUrlRef.current); audioUrlRef.current = null; }
    };
  }, []);

  return { recording, recordingTime, audioBlob, audioUrl, startRecording, stopRecording, reset };
}

/** 逐题对错明细列表（结果页与记录详情共用） */
function ListeningDetailList({
  details, showDialogue, onPlayDialogue,
}: {
  details: ListeningDetail[];
  showDialogue?: boolean;
  onPlayDialogue?: (text: string) => void;
}) {
  return (
    <List
      dataSource={details}
      renderItem={(d) => (
        <List.Item>
          <div style={{ width: "100%" }}>
            {/* 短对话模式：展示每题 dialogue */}
            {showDialogue && d.dialogue && (
              <div style={{
                background: "#f6f8fa", padding: 10, borderRadius: 6, marginBottom: 8,
                borderLeft: "3px solid #1677ff",
              }}>
                {onPlayDialogue && (
                  <Button
                    size="small"
                    type="link"
                    icon={<PlayCircleOutlined />}
                    onClick={() => onPlayDialogue(d.dialogue!)}
                    style={{ padding: 0, marginBottom: 4 }}
                  >
                    播放对话
                  </Button>
                )}
                <Paragraph
                  style={{
                    whiteSpace: "pre-wrap", margin: 0, fontSize: 12,
                    color: "#555", fontStyle: "italic",
                  }}
                >
                  {d.dialogue}
                </Paragraph>
              </div>
            )}
            <Space align="start">
              {d.is_correct ? (
                <CheckCircleOutlined style={{ color: "#52c41a", marginTop: 4 }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#ff4d4f", marginTop: 4 }} />
              )}
              <Text strong>{d.question_id}. {d.question}</Text>
            </Space>
            <div style={{ marginLeft: 24, marginTop: 4 }}>
              {d.options && Object.entries(d.options).map(([k, v]) => (
                <div key={k}>
                  <Text
                    type={k === d.correct_answer ? "success" : undefined}
                    delete={k === d.user_answer && !d.is_correct}
                  >
                    {k}. {v}
                  </Text>
                </div>
              ))}
              <Space style={{ marginTop: 4 }} wrap>
                <Tag color={d.is_correct ? "success" : "error"}>
                  你的作答：{d.user_answer || "(未作答)"}
                </Tag>
                <Tag color="blue">正确答案：{d.correct_answer}</Tag>
              </Space>
            </div>
          </div>
        </List.Item>
      )}
    />
  );
}

/** 作业记录详情弹窗（展示听力原文/题目/作答/对错） */
function RecordDetailModal({
  recordId, open, onClose,
}: { recordId: number | null; open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false);
  const [record, setRecord] = useState<OralRecordDetail | null>(null);
  const player = useTranscriptPlayer("listening");
  // 请求代次：快速切换记录时，旧请求晚返回会覆盖新记录（D8 竞态）
  const requestSeqRef = useRef(0);

  useEffect(() => {
    if (!open || recordId == null) return;
    player.stop();
    const seq = ++requestSeqRef.current;
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then((data) => {
        // 期间已切换到其他记录，丢弃本次过期响应
        if (seq !== requestSeqRef.current) return;
        setRecord(data);
      })
      .catch(() => {
        if (seq === requestSeqRef.current) message.error("加载记录详情失败");
      })
      .finally(() => {
        if (seq === requestSeqRef.current) setLoading(false);
      });
  }, [open, recordId]);

  const detail = record?.detail;
  const transcript = detail?.transcript;
  const items = (detail?.details as ListeningDetail[] | undefined) || [];

  const handleClose = () => { player.stop(); onClose(); };

  return (
    <Modal
      title={record?.name || "作业详情"}
      open={open}
      onCancel={handleClose}
      afterClose={() => player.stop()}
      footer={null}
      width={720}
    >
      {loading && <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>}
      {!loading && record && (
        <>
          <Space wrap style={{ marginBottom: 12 }}>
            {record.score && <Tag color="blue">成绩 {record.score}</Tag>}
            {record.grade_level && <Tag color="purple">{record.grade_level}</Tag>}
            {detail?.question_type && <Tag>{String(detail.question_type)}</Tag>}
            {detail?.difficulty && <Tag>{String(detail.difficulty)}</Tag>}
            {detail?.grade && <Tag color="green">{String(detail.grade)}</Tag>}
          </Space>
          {/* 听力原文：短对话题型每题自带独立对话，顶层 transcript 为空是正常设计 */}
          {transcript ? (
            <>
              <Divider orientation="left">📜 听力原文</Divider>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<PlayCircleOutlined />} onClick={() => player.speak(transcript)}>
                  播放原文
                </Button>
                <PlaybackRateControl value={player.playbackRate} onChange={player.setPlaybackRate} width={90} />
                {player.playState === "loading" && (
                  <Button size="small" disabled icon={<PauseCircleOutlined />}>生成语音中...</Button>
                )}
                {player.playState === "playing" && (
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
                )}
                {player.playState === "paused" && (
                  <Button size="small" icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
                )}
              </Space>
              {player.playState === "loading" && (
                <div style={{ marginBottom: 8 }}>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} />} size="small" />
                  {" "}<Text type="secondary">正在生成语音，请稍候...</Text>
                </div>
              )}
              {(player.playState === "playing" || player.playState === "paused") && (
                <Progress percent={player.progress} size="small" style={{ marginBottom: 8 }} />
              )}
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 12, borderRadius: 6 }}>
                {transcript}
              </Paragraph>
            </>
          ) : detail?.question_type === "短对话" ? (
            <Alert type="info" showIcon style={{ marginBottom: 12 }}
              message="此题型每题包含独立对话，请查看下方的题目详情" />
          ) : (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message="该记录未保存听力原文（旧数据）" />
          )}
          {items.length > 0 && (
            <>
              <Divider orientation="left">📝 题目与作答</Divider>
              <ListeningDetailList
                details={items}
                showDialogue={detail?.question_type === "短对话"}
                onPlayDialogue={(text) => player.speakDialogue(text)}
              />
            </>
          )}
        </>
      )}
    </Modal>
  );
}

/** 单词听写记录详情弹窗（展示听力原文 broadcast_text + 逐题题目与作答） */
function DictationRecordDetailModal({
  recordId, open, onClose,
}: { recordId: number | null; open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false);
  const [record, setRecord] = useState<OralRecordDetail | null>(null);
  const player = useTranscriptPlayer("dictation", 0.8);
  // 请求代次：快速切换记录时，旧请求晚返回会覆盖新记录（D8 竞态）
  const requestSeqRef = useRef(0);

  useEffect(() => {
    if (!open || recordId == null) return;
    player.stop();
    const seq = ++requestSeqRef.current;
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then((data) => {
        // 期间已切换到其他记录，丢弃本次过期响应
        if (seq !== requestSeqRef.current) return;
        setRecord(data);
      })
      .catch(() => {
        if (seq === requestSeqRef.current) message.error("加载记录详情失败");
      })
      .finally(() => {
        if (seq === requestSeqRef.current) setLoading(false);
      });
  }, [open, recordId]);

  const detail = record?.detail;
  const broadcastText = detail?.broadcast_text || "";
  const items = (detail?.details as DictationDetailItem[] | undefined) || [];

  const handleClose = () => { player.stop(); onClose(); };

  return (
    <Modal
      title={record?.name || "单词听写详情"}
      open={open}
      onCancel={handleClose}
      afterClose={() => player.stop()}
      footer={null}
      width={720}
    >
      {loading && <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>}
      {!loading && record && (
        <>
          <Space wrap style={{ marginBottom: 12 }}>
            {record.score && <Tag color="blue">得分 {record.score}</Tag>}
            {detail?.word_scope && <Tag color="geekblue">{String(detail.word_scope)}</Tag>}
            {detail?.direction && <Tag color="orange">{String(detail.direction)}</Tag>}
            {detail?.difficulty && (
              <Tag color={detail.difficulty === "困难" ? "red" : detail.difficulty === "简单" ? "green" : "gold"}>
                {String(detail.difficulty)}
              </Tag>
            )}
            {detail?.answer_mode && (
              <Tag>{detail.answer_mode === "upload" ? "上传图片作答" : "键盘作答"}</Tag>
            )}
          </Space>
          {/* 听力原文（老师口语化播报文本） */}
          {broadcastText ? (
            <>
              <Divider orientation="left">📜 听力原文</Divider>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<PlayCircleOutlined />} onClick={() => player.speak(broadcastText, "mixed")}>
                  播放原文
                </Button>
                <PlaybackRateControl value={player.playbackRate} onChange={player.setPlaybackRate} width={90} />
                {player.playState === "loading" && (
                  <Button size="small" disabled icon={<PauseCircleOutlined />}>生成语音中...</Button>
                )}
                {player.playState === "playing" && (
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
                )}
                {player.playState === "paused" && (
                  <Button size="small" icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
                )}
              </Space>
              {(player.playState === "playing" || player.playState === "paused") && (
                <Progress percent={player.progress} size="small" style={{ marginBottom: 8 }} />
              )}
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 12, borderRadius: 6 }}>
                {broadcastText}
              </Paragraph>
            </>
          ) : (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message="该记录未保存听力原文（旧数据）" />
          )}
          {/* 题目与作答 */}
          {items.length > 0 && (
            <>
              <Divider orientation="left">📝 题目与作答</Divider>
              <List
                size="small"
                dataSource={items}
                renderItem={(it) => (
                  <List.Item>
                    <Space wrap>
                      <Text type="secondary">第 {it.index} 题</Text>
                      {it.prompt_lang && <Tag color="purple">{it.prompt_lang}</Tag>}
                      <Text>{it.question}</Text>
                      {it.is_correct
                        ? <CheckCircleOutlined style={{ color: "#52c41a" }} />
                        : <CloseCircleOutlined style={{ color: "#ff4d4f" }} />}
                      <Text
                        delete={!it.is_correct}
                        type={it.is_correct ? "success" : "danger"}
                      >
                        {it.user_answer || "(空)"}
                      </Text>
                      {!it.is_correct && (
                        <>
                          <Text>→</Text>
                          <Text strong type="success">{it.correct_answer}</Text>
                        </>
                      )}
                    </Space>
                  </List.Item>
                )}
              />
            </>
          )}
        </>
      )}
    </Modal>
  );
}

/** 作业记录列表（展示在每个面板下方，refreshKey 变化时重新拉取）
 *
 * 统一采用卡片格式（与作业记录一致）：
 * - 英语听力记录：含学段/题型筛选，点击名称或「查看详情」可查看听力原文/题目/作答/对错
 * - 单词听写记录：含测试方向/词库范围/难度筛选，点击名称或「查看详情」可查看听力原文/题目与作答
 * - 普通话测评记录：可点击查看测评等级、音频、转写文本、AI评语
 * 每条记录末尾有删除按钮，底部统一分页。
 */
function OralRecordsList({
  category, refreshKey, title, showMandarinDetail, showFilters, showDictationFilters, onDelete, active,
}: {
  category: string; refreshKey: number;
  title?: string;
  showMandarinDetail?: boolean;
  showFilters?: boolean;
  showDictationFilters?: boolean;
  onDelete?: (id: number) => Promise<void>;
  /** 所在子板块是否激活：面板常驻挂载（隐藏而非卸载），未激活时不发列表请求 */
  active?: boolean;
}) {
  const [records, setRecords] = useState<OralRecord[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeMandarinId, setActiveMandarinId] = useState<number | null>(null);
  const [activeDictationId, setActiveDictationId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  /** 编辑（重命名）弹窗状态 */
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  /** 筛选状态（仅 showFilters 模式使用） */
  const [filterGrade, setFilterGrade] = useState<string | undefined>(undefined);
  const [filterType, setFilterType] = useState<string | undefined>(undefined);
  /** 筛选状态（仅 showDictationFilters 模式使用） */
  const [filterDirection, setFilterDirection] = useState<string | undefined>(undefined);
  const [filterScope, setFilterScope] = useState<string | undefined>(undefined);
  const [filterDifficulty, setFilterDifficulty] = useState<string | undefined>(undefined);
  const clickable = category === "英语听力";
  const mandarinClickable = category === "普通话测评" && showMandarinDetail;
  const dictationClickable = category === "单词听写" && showDictationFilters;

  const load = useCallback(async () => {
    try {
      const params: {
        category: string; grade_level?: string; question_type?: string;
        word_scope?: string; direction?: string; difficulty?: string;
      } = { category };
      if (showFilters && filterGrade) params.grade_level = filterGrade;
      if (showFilters && filterType) params.question_type = filterType;
      if (showDictationFilters && filterDirection) params.direction = filterDirection;
      if (showDictationFilters && filterScope) params.word_scope = filterScope;
      if (showDictationFilters && filterDifficulty) params.difficulty = filterDifficulty;
      const data = await oralService.listRecords(params);
      setRecords(data || []);
    } catch (err) {
      console.error("加载口语测评记录失败:", err);
    }
  }, [category, showFilters, filterGrade, filterType, showDictationFilters, filterDirection, filterScope, filterDifficulty]);

  // 仅在所在子板块激活时加载；首次切换到该板块时自动拉取，之后由 refreshKey 驱动刷新
  useEffect(() => {
    if (active) load();
  }, [load, refreshKey, active]);

  /** 筛选变化时回到第一页 */
  const updateFilter = (setter: (v: string | undefined) => void) => (v: string | undefined) => {
    setter(v);
    setPage(1);
  };

  /** 当前是否有筛选条件（决定是否显示"重置"按钮） */
  const hasFilter = !!(
    (showFilters && (filterGrade || filterType)) ||
    (showDictationFilters && (filterDirection || filterScope || filterDifficulty))
  );

  /** 一键清空全部筛选并回到第一页 */
  const resetFilters = () => {
    setFilterGrade(undefined);
    setFilterType(undefined);
    setFilterDirection(undefined);
    setFilterScope(undefined);
    setFilterDifficulty(undefined);
    setPage(1);
  };

  /** 处理删除：调用父组件回调，成功后刷新列表 */
  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      if (onDelete) {
        await onDelete(id);
      }
      message.success("已删除");
      setRecords((prev) => prev.filter((r) => r.id !== id));
    } catch {
      message.error("删除失败");
    } finally {
      setDeleting(null);
    }
  };

  /** 处理重命名：调用接口，成功后更新列表中的名称并关闭弹窗 */
  const handleRename = async () => {
    if (editingId == null) return;
    const trimmed = editName.trim();
    if (!trimmed) {
      message.warning("名称不能为空");
      return;
    }
    setRenaming(true);
    try {
      await oralService.renameRecord(editingId, trimmed);
      message.success("已修改");
      setEditModalOpen(false);
      setEditingId(null);
      setRecords((prev) => prev.map((r) => (r.id === editingId ? { ...r, name: trimmed } : r)));
    } catch {
      message.error("修改失败");
    } finally {
      setRenaming(false);
    }
  };

  /** 构建"更多"下拉菜单（编辑 / 删除），供三种卡片复用 */
  const buildMoreMenu = (r: OralRecord) => ({
    items: [
      {
        key: "edit",
        label: "编辑",
        icon: <EditOutlined />,
        onClick: () => {
          setEditingId(r.id);
          setEditName(r.name);
          setEditModalOpen(true);
        },
      },
      {
        key: "delete",
        label: "删除",
        danger: true,
        icon: <DeleteOutlined />,
        onClick: () => {
          Modal.confirm({
            title: "确定删除该记录？",
            content: "删除后不可恢复",
            okText: "确认删除",
            okType: "danger",
            cancelText: "取消",
            onOk: () => handleDelete(r.id),
          });
        },
      },
    ],
  });

  /** 分页数据：删除/筛选后 records 变短，page 可能越界（slice 返回空列表且分页器页码异常），
   *  钳制到有效页范围（[1, 总页数]，无数据时固定第 1 页） */
  const safePage = Math.min(page, Math.max(1, Math.ceil(records.length / pageSize)));
  const pagedRecords = records.slice((safePage - 1) * pageSize, safePage * pageSize);

  /** 打开详情 */
  const openDetail = (r: OralRecord) => {
    if (clickable) setActiveId(r.id);
    if (mandarinClickable) setActiveMandarinId(r.id);
    if (dictationClickable) setActiveDictationId(r.id);
  };

  /** 渲染测评记录卡片（英语听力/单词听写/普通话测评共用一套卡片结构，仅 meta 标签组合不同）。
   *  三种卡片的差异只存在于标签区，合并后以字段存在与否自然区分，避免三份重复 JSX */
  const renderRecordCard = (r: OralRecord) => {
    const [scoreNum, scoreDen] = parseOralScore(r.score);
    return (
    <div className="or-card" key={r.id}>
      <div className="or-card-badge">
        <span className="or-badge-score">{scoreNum ?? "—"}</span>
        <span className="or-badge-sep">/</span>
        <span className="or-badge-total">{scoreDen ?? r.full_score ?? "—"}</span>
      </div>
      <div className="or-card-body">
        <div className="or-card-info">
          <div className="or-card-row or-card-row-title">
            <span className="or-card-title" onClick={() => openDetail(r)}>{r.name}</span>
          </div>
          <div className="or-card-row or-card-row-meta">
            {r.grade_level && <span className="or-card-meta-tag">{r.grade_level}</span>}
            {r.question_type && <span className="or-card-meta-tag">{r.question_type}</span>}
            {r.word_scope && <span className="or-card-meta-tag">{r.word_scope}</span>}
            {r.direction && <span className="or-card-meta-tag">{r.direction}</span>}
            {r.difficulty && (
              <span className="or-card-meta-tag">
                {r.difficulty === "困难" ? "🔴 " : r.difficulty === "简单" ? "🟢 " : "🟡 "}
                {r.difficulty}
              </span>
            )}
          </div>
          <div className="or-card-row or-card-row-time">
            {formatDate(r.created_at)}
          </div>
        </div>
        <div className="or-card-actions">
          <Button className="or-action-view" icon={<EyeOutlined />} onClick={() => openDetail(r)}>
            查看
          </Button>
          <Dropdown
            menu={buildMoreMenu(r)}
            trigger={["click"]}
          >
            <Button className="or-action-delete">
              更多
            </Button>
          </Dropdown>
        </div>
      </div>
    </div>
    );
  };

  /** 根据类别选择渲染函数（三种卡片已合并，仅保留入口） */
  const renderCard = renderRecordCard;

  return (
    <Card className="oral-records" style={{ marginTop: 16 }} title={title || "📄 作业记录"}>
      {/* 筛选栏（英语听力模式） */}
      {showFilters && (
        <div className="or-filter-bar">
          <div className="or-filter-fields">
            <div className="or-filter-field">
              <span className="or-filter-label">学段</span>
              <Select
                allowClear
                placeholder="全部"
                value={filterGrade}
                onChange={updateFilter(setFilterGrade)}
                options={[
                  { label: "小学", value: "小学" },
                  { label: "初中", value: "初中" },
                  { label: "高中", value: "高中" },
                ]}
              />
            </div>
            <div className="or-filter-field">
              <span className="or-filter-label">题型</span>
              <Select
                allowClear
                placeholder="全部"
                value={filterType}
                onChange={updateFilter(setFilterType)}
                options={[
                  { label: "短对话", value: "短对话" },
                  { label: "长对话", value: "长对话" },
                  { label: "短文理解", value: "短文理解" },
                ]}
              />
            </div>
            {hasFilter && (
              <Button type="text" className="or-filter-reset" icon={<ReloadOutlined />} onClick={resetFilters}>
                重置筛选
              </Button>
            )}
          </div>
        </div>
      )}
      {/* 筛选栏（单词听写模式） */}
      {showDictationFilters && (
        <div className="or-filter-bar">
          <div className="or-filter-fields">
            <div className="or-filter-field">
              <span className="or-filter-label">测试方向</span>
              <Select
                allowClear
                placeholder="全部"
                value={filterDirection}
                onChange={updateFilter(setFilterDirection)}
                options={[
                  { label: "汉译英", value: "汉译英" },
                  { label: "英译汉", value: "英译汉" },
                  { label: "默写单词", value: "默写单词" },
                  { label: "中英混合", value: "中英混合" },
                ]}
              />
            </div>
            <div className="or-filter-field">
              <span className="or-filter-label">难度</span>
              <Select
                allowClear
                placeholder="全部"
                value={filterDifficulty}
                onChange={updateFilter(setFilterDifficulty)}
                options={[
                  { label: "简单", value: "简单" },
                  { label: "中等", value: "中等" },
                  { label: "困难", value: "困难" },
                ]}
              />
            </div>
            <div className="or-filter-field">
              <span className="or-filter-label">词库范围</span>
              <Select
                allowClear
                placeholder="全部"
                value={filterScope}
                onChange={updateFilter(setFilterScope)}
                options={[
                  { label: "小学必备词汇", value: "小学必备词汇" },
                  { label: "初中必备词汇", value: "初中必备词汇" },
                  { label: "高中必备词汇", value: "高中必备词汇" },
                  { label: "四级词汇", value: "四级词汇" },
                ]}
              />
            </div>
            {hasFilter && (
              <Button type="text" className="or-filter-reset" icon={<ReloadOutlined />} onClick={resetFilters}>
                重置筛选
              </Button>
            )}
          </div>
        </div>
      )}
      {/* 卡片列表 */}
      <div className="or-card-list">
        {records.length === 0 ? (
          <div className="or-empty">暂无记录</div>
        ) : (
          pagedRecords.map(renderCard)
        )}
      </div>
      {/* 分页 */}
      {records.length > pageSize && (
        <div className="or-pagination">
          <Pagination
            current={safePage}
            pageSize={pageSize}
            total={records.length}
            onChange={setPage}
            showTotal={(total) => `共 ${total} 条`}
          />
        </div>
      )}
      {/* 详情弹窗 */}
      {clickable && (
        <RecordDetailModal
          recordId={activeId}
          open={activeId != null}
          onClose={() => setActiveId(null)}
        />
      )}
      {mandarinClickable && (
        <MandarinRecordDetailModal
          recordId={activeMandarinId}
          open={activeMandarinId != null}
          onClose={() => setActiveMandarinId(null)}
        />
      )}
      {dictationClickable && (
        <DictationRecordDetailModal
          recordId={activeDictationId}
          open={activeDictationId != null}
          onClose={() => setActiveDictationId(null)}
        />
      )}
      {/* 重命名（编辑）弹窗 */}
      <Modal
        title="修改作业名称"
        open={editModalOpen}
        onCancel={() => setEditModalOpen(false)}
        onOk={handleRename}
        confirmLoading={renaming}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <div style={{ marginTop: 16 }}>
          <Text type="secondary">请输入新的作业名称：</Text>
          <Input
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            maxLength={128}
            placeholder="请输入作业名称"
            style={{ marginTop: 8 }}
            onPressEnter={handleRename}
          />
        </div>
      </Modal>
    </Card>
  );
}

/** 维度得分展示块（普通话评测详情 / 评测结果共用，避免两处重复渲染逻辑） */
function DimensionScoreBlock({
  scores,
  fullScore,
  nameMap,
  dividerText,
}: {
  scores: Record<string, unknown>;
  fullScore: number;
  nameMap: Record<string, string>;
  dividerText?: string;
}) {
  return (
    <>
      <Divider orientation="left">{dividerText || "📊 维度得分"}</Divider>
      {Object.entries(scores).map(([k, v]) => {
        const percent = Math.round((Number(v) / fullScore) * 100);
        return (
          <div key={k} style={{ marginBottom: 12 }}>
            <Space>
              <Text>{nameMap[k] || k}</Text>
              <Text strong>{String(v)}/{fullScore}</Text>
            </Space>
            <Progress
              percent={percent}
              size="small"
              status={percent >= 72 ? "success" : percent >= 48 ? "normal" : "exception"}
            />
          </div>
        );
      })}
    </>
  );
}

/** 普通话测评记录详情弹窗（展示等级、音频、转写文本、AI评语） */
function MandarinRecordDetailModal({
  recordId, open, onClose,
}: { recordId: number | null; open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false);
  const [record, setRecord] = useState<OralRecordDetail | null>(null);
  // 请求代次：快速切换记录时，旧请求晚返回会覆盖新记录（D8 竞态）
  const requestSeqRef = useRef(0);

  useEffect(() => {
    if (!open || recordId == null) return;
    const seq = ++requestSeqRef.current;
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then((data) => {
        // 期间已切换到其他记录，丢弃本次过期响应
        if (seq !== requestSeqRef.current) return;
        setRecord(data);
      })
      .catch(() => {
        if (seq === requestSeqRef.current) message.error("加载记录详情失败");
      })
      .finally(() => {
        if (seq === requestSeqRef.current) setLoading(false);
      });
  }, [open, recordId]);

  const detail = record?.detail || {};
  const dimNameMap: Record<string, string> = {
    pronunciation: "声韵准确度",
    tone: "声调准确度",
    grammar: "词汇语法规范度",
    fluency: "流畅度",
    completeness: "完整度",
    intonation: "语调自然度",
  };
  // 各维度满分：讯飞评测为 100 分制，LLM 评测为 25 分制。
  // dimension_full_score 字段只在新讯飞记录中返回；旧 LLM 评测记录无此字段，
  // 若一律 fallback 100，会把 22/25 的旧记录渲染成 22/100（得分率错 4 倍）。
  // 故按评测引擎区分：讯飞 → 100（新记录以字段值为准），其他（LLM 评测）→ 25
  const dimFull = detail.engine === "xfyun_ise"
    ? (Number(detail.dimension_full_score) || 100)
    : 25;
  const errorChars = Array.isArray(detail.error_chars) ? (detail.error_chars as string[]) : [];

  return (
    <Modal
      title={record?.name || "普通话测评详情"}
      open={open}
      onCancel={onClose}
      footer={null}
      width={680}
    >
      {loading && <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>}
      {!loading && record && (
        <>
          {/* 基本信息标签 */}
          <Space wrap style={{ marginBottom: 16 }}>
            {record.score && <Tag color="blue">成绩：{record.score}</Tag>}
            {detail.test_level ? <Tag color="purple">目标等级：{String(detail.test_level)}</Tag> : null}
            {detail.level ? <Tag color="green">评测等级：{String(detail.level)}</Tag> : null}
            {detail.evaluation_mode ? (
              <Tag>{detail.evaluation_mode === "ai_generated" ? "AI生成文本" : detail.evaluation_mode === "free_speech" ? "自行发挥" : String(detail.evaluation_mode)}</Tag>
            ) : null}
            {detail.total_score != null ? <Tag color="orange">总分：{String(detail.total_score)}</Tag> : null}
            {detail.engine === "xfyun_ise" ? <Tag color="cyan">讯飞语音评测</Tag> : null}
          </Space>

          {/* 维度得分 */}
          {detail.dimension_scores && typeof detail.dimension_scores === "object" && Object.keys(detail.dimension_scores).length > 0 && (
            <DimensionScoreBlock
              scores={detail.dimension_scores as Record<string, unknown>}
              fullScore={dimFull}
              nameMap={dimNameMap}
            />
          )}

          {/* 朗读有误的字词（讯飞评测） */}
          {errorChars.length > 0 && (
            <>
              <Divider orientation="left">❌ 朗读有误的字词</Divider>
              <Space wrap>
                {errorChars.map((c, i) => <Tag key={i} color="red">{c}</Tag>)}
              </Space>
            </>
          )}

          {/* 参考文本（AI生成文本模式） */}
          {detail.reference_text ? (
            <>
              <Divider orientation="left">📖 参考文本</Divider>
              <Paragraph style={{ background: "#fafafa", padding: 12, borderRadius: 6, whiteSpace: "pre-wrap" }}>
                {String(detail.reference_text)}
              </Paragraph>
            </>
          ) : null}

          {/* 转写文本 */}
          {detail.transcribed_text ? (
            <>
              <Divider orientation="left">📝 语音转写</Divider>
              <Paragraph style={{ background: "#fafafa", padding: 12, borderRadius: 6, whiteSpace: "pre-wrap" }}>
                {String(detail.transcribed_text)}
              </Paragraph>
            </>
          ) : null}

          {/* 音频回放：oral_audio/ 为私有目录，强制鉴权，
              原生 <audio> 无法带 Authorization 头，用 AuthedAudio 下载后播放 */}
          {detail.audio_url ? (
            <>
              <Divider orientation="left">🔊 录音回放</Divider>
              <AuthedAudio src={`/api/v1/files/${String(detail.audio_url)}`} />
            </>
          ) : null}

          {/* AI 评语 */}
          {detail.ai_comment ? (
            <>
              <Divider orientation="left">🤖 AI 评语</Divider>
              <Alert type="info" message={String(detail.ai_comment)} />
            </>
          ) : null}

          {/* 改进建议 */}
          {detail.suggestions && Array.isArray(detail.suggestions) && (detail.suggestions as unknown[]).length > 0 && (
            <>
              <Divider orientation="left">💡 改进建议</Divider>
              <List
                size="small"
                dataSource={detail.suggestions as string[]}
                renderItem={(s, i) => (
                  <List.Item>
                    <Text>{i + 1}. {typeof s === "string" ? s : JSON.stringify(s)}</Text>
                  </List.Item>
                )}
              />
            </>
          )}
        </>
      )}
    </Modal>
  );
}

/** 英语听力面板
 *
 * 新流程：选好题型/难度/题数 → AI 生成一段完整对话并从中提取题目。
 * 答题阶段不显示原文（只能播放整段对话音频）；批改后才展示听力原文与逐题对错。
 */
function ListeningPanel({ active }: { active?: boolean }) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [meta, setMeta] = useState<{ question_type: string; difficulty: string; grade_level?: string }>({ question_type: "短对话", difficulty: "中等" });
  const [questions, setQuestions] = useState<ListeningQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [result, setResult] = useState<ListeningResult | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [playingIndex, setPlayingIndex] = useState<number | null>(null);  // 当前正在播放的题目索引
  const [minQuestionCount, setMinQuestionCount] = useState(1);  // 短对话最少1题，其他题型最少2题
  const player = useTranscriptPlayer("listening");
  /** 音频预缓存：key=题目索引, value=Blob URL */
  const audioCacheRef = useRef<Map<number, string>>(new Map());
  const prefetchingRef = useRef(false);
  const generationRef = useRef(0);  // 生成计数器，防止旧预加载数据污染新缓存

  // 播放结束后重置播放索引
  useEffect(() => {
    if (player.playState === "idle") {
      setPlayingIndex(null);
    }
  }, [player.playState]);

  // 组件卸载或重新生成时清理缓存 blob URL
  useEffect(() => {
    return () => {
      audioCacheRef.current.forEach((url) => URL.revokeObjectURL(url));
      audioCacheRef.current.clear();
      prefetchingRef.current = false;
    };
  }, [questions]);

  /** 预加载所有短对话题目的 TTS 音频到缓存中 */
  const preloadAllDialogues = useCallback(async () => {
    if (prefetchingRef.current) return;  // 防止并发预加载
    prefetchingRef.current = true;
    const gen = generationRef.current;  // 记录当前生成代数
    const rateParam = "+0%";
    const tasks = questions.map(async (q, i) => {
      if (!q.dialogue || audioCacheRef.current.has(i)) return;
      try {
        const fetchUrl = `/api/v1/oral/tts-dialogue?text=${encodeURIComponent(q.dialogue)}&rate=${encodeURIComponent(rateParam)}`;
        const response = await ttsFetch(fetchUrl);
        // 如果在此期间生成了新题目，丢弃旧结果
        if (gen !== generationRef.current) return;
        if (response.ok) {
          const blob = await response.blob();
          if (blob.size > 0 && gen === generationRef.current) {
            const blobUrl = URL.createObjectURL(blob);
            audioCacheRef.current.set(i, blobUrl);
          }
        }
      } catch { /* 预加载静默失败，不影响主播放流程 */ }
    });
    await Promise.allSettled(tasks);
    prefetchingRef.current = false;
  }, [questions]);

  /** 播放题目对话（优先使用缓存，同时触发全量预加载） */
  const playDialogueWithCache = useCallback((index: number, dialogue: string) => {
    setPlayingIndex(index);
    // 检查缓存
    const cached = audioCacheRef.current.get(index);
    if (cached) {
      player.playBlob(cached);
    } else {
      player.speakDialogue(dialogue);
    }
    // 首次播放时触发后台预加载所有对话
    if (audioCacheRef.current.size === 0) {
      preloadAllDialogues();
    }
  }, [player, preloadAllDialogues]);

  const reset = useCallback(() => {
    player.stop();
    setPlayingIndex(null);
    setQuestions([]);
    setResult(null);
    setAnswers({});
    setTranscript("");
  }, [player.stop]);

  const generate = useCallback(async () => {
    const vals = await form.validateFields().catch(() => null);
    if (!vals) return;
    player.stop();
    setPlayingIndex(null);
    setLoading(true);
    setResult(null);
    setAnswers({});
    setTranscript("");
    try {
      // 递增生成计数器，使旧预加载任务失效
      generationRef.current += 1;
      const data = await oralService.generateListening(vals);
      setQuestions(data.questions || []);
      setTranscript(data.transcript || "");
      setMeta({ question_type: vals.question_type, difficulty: vals.difficulty, grade_level: vals.grade_level });
      message.success(`已生成 ${data.questions?.length || 0} 道听力题`);
    } catch {
      message.error("生成听力试卷失败");
    } finally {
      setLoading(false);
    }
  }, [form, player.stop]);

  const submit = useCallback(async () => {
    if (questions.length === 0) return;
    player.stop();
    setPlayingIndex(null);
    const answerList = questions.map((_, i) => answers[i] || "");
    setSubmitting(true);
    try {
      const data = await oralService.submitListening(questions, answerList, {
        transcript,
        question_type: meta.question_type,
        difficulty: meta.difficulty,
        grade_level: meta.grade_level,
      });
      setResult(data);
      setRefreshKey((k) => k + 1);
    } catch {
      message.error("提交批改失败");
    } finally {
      setSubmitting(false);
    }
  }, [questions, answers, transcript, meta, player.stop]);

  /** 删除记录回调：调用 API 删除 */
  const handleDelete = useCallback(async (id: number) => {
    await oralService.deleteRecord(id);
  }, []);

  return (
    <div>
      <Card>
        <Form
          form={form}
          layout="inline"
          initialValues={{ question_type: "短对话", difficulty: "中等", question_count: 5, grade_level: undefined }}
          onValuesChange={(changed) => {
            if (changed.question_type) {
              // 短对话至少1题，长对话/短文理解至少2题
              const newMin = changed.question_type === "短对话" ? 1 : 2;
              setMinQuestionCount(newMin);
              // 如果当前题数小于新的最小值，自动调整
              const currentCount = form.getFieldValue("question_count");
              if (currentCount < newMin) {
                form.setFieldsValue({ question_count: newMin });
              }
            }
          }}
        >
          <Form.Item label="学段" name="grade_level" style={{ marginBottom: 0 }}>
            <Select
              allowClear
              placeholder="不限"
              options={[
                { label: "小学", value: "小学" },
                { label: "初中", value: "初中" },
                { label: "高中", value: "高中" },
              ]}
              style={{ width: 90 }}
            />
          </Form.Item>
          <Form.Item label="题型" name="question_type" style={{ marginBottom: 0 }}>
            <Select options={[
              { label: "短对话", value: "短对话" },
              { label: "长对话", value: "长对话" },
              { label: "短文理解", value: "短文理解" },
            ]} style={{ width: 110 }} />
          </Form.Item>
          <Form.Item label="难度" name="difficulty" style={{ marginBottom: 0 }}>
            <Select options={[
              { label: "简单", value: "简单" },
              { label: "中等", value: "中等" },
              { label: "困难", value: "困难" },
            ]} style={{ width: 90 }} />
          </Form.Item>
          <Form.Item label="题数" name="question_count" style={{ marginBottom: 0 }}>
            <InputNumber min={minQuestionCount} max={5} />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" loading={loading} onClick={generate} icon={<SoundOutlined />}>
              生成听力试卷
            </Button>
          </Form.Item>
          <Form.Item label="播放速度" style={{ marginBottom: 0 }}>
            <PlaybackRateControl value={player.playbackRate} onChange={player.setPlaybackRate} />
          </Form.Item>
        </Form>
      </Card>

      {loading && <div style={{ textAlign: "center", padding: 40 }}><Spin tip="AI 正在生成听力对话..." /></div>}

      {questions.length > 0 && !result && (
        <Card style={{ marginTop: 16 }} title="🎧 听力作答">
          {/* 短对话：每题独立 dialogue + 播放按钮，显示在题目上方 */}
          {/* 长对话/短文理解：共用一段 transcript，一个总播放按钮 */}
          {meta.question_type !== "短对话" && transcript && (
            <>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="请点击下方按钮播放听力对话，听后作答（作答阶段不显示原文）"
              />
              <Space style={{ marginBottom: 8 }}>
                <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => player.speak(transcript)}>
                  播放听力对话
                </Button>
                {player.playState === "loading" && (
                  <Button disabled icon={<PauseCircleOutlined />}>生成语音中...</Button>
                )}
                {player.playState === "playing" && (
                  <Button icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
                )}
                {player.playState === "paused" && (
                  <Button icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
                )}
              </Space>
              {player.playState === "loading" && (
                <div style={{ marginBottom: 16 }}>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} />} size="small" />
                  {" "}<Text type="secondary">正在生成语音，请稍候...</Text>
                </div>
              )}
              {(player.playState === "playing" || player.playState === "paused") && (
                <Progress percent={player.progress} size="small" style={{ marginBottom: 16 }} />
              )}
            </>
          )}
          {meta.question_type === "短对话" && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="每道题有一段独立对话，点击题目上方的播放按钮收听。M 为男声，W 为女声。"
            />
          )}
          <List
            dataSource={questions}
            renderItem={(q, i) => (
              <List.Item>
                <div style={{ width: "100%" }}>
                  {/* 短对话模式：每题上方显示独立播放按钮（不显示听力原文） */}
                  {meta.question_type === "短对话" && q.dialogue && (
                    <div style={{
                      background: "#f6f8fa", padding: 12, borderRadius: 6, marginBottom: 12,
                      borderLeft: "3px solid #1677ff",
                    }}>
                      <Space style={{ marginBottom: 0 }}>
                        <Button
                          size="small"
                          type="primary"
                          icon={<PlayCircleOutlined />}
                          onClick={() => playDialogueWithCache(i, q.dialogue!)}
                        >
                          播放对话 {i + 1}
                        </Button>
                        {playingIndex === i && player.playState === "loading" && (
                          <Text type="secondary" style={{ fontSize: 12 }}>生成语音中...</Text>
                        )}
                        {playingIndex === i && (player.playState === "playing" || player.playState === "paused") && (
                          <Progress percent={player.progress} size="small" style={{ width: 80, marginBottom: 0 }} />
                        )}
                      </Space>
                    </div>
                  )}
                  <Text strong>{i + 1}. {q.question}</Text>
                  {q.options && (
                    <Radio.Group
                      style={{ display: "block", marginTop: 8, marginLeft: 16 }}
                      onChange={(e) => setAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                      value={answers[i]}
                    >
                      <Space direction="vertical">
                        {Object.entries(q.options).map(([k, v]) => (
                          <Radio key={k} value={k}>{k}. {v}</Radio>
                        ))}
                      </Space>
                    </Radio.Group>
                  )}
                  {!q.options && (
                    <TextArea
                      rows={2}
                      style={{ marginTop: 8 }}
                      placeholder="请输入你的答案..."
                      onChange={(e) => setAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                    />
                  )}
                </div>
              </List.Item>
            )}
          />
          <Button type="primary" block size="large" loading={submitting} onClick={submit} style={{ marginTop: 16 }}>
            提交批改
          </Button>
        </Card>
      )}

      {result && (
        <Card style={{ marginTop: 16 }} title="📊 批改结果">
          <Result
            status={result.correct_rate >= 0.7 ? "success" : "info"}
            title={`正确率：${Math.round(result.correct_rate * 100)}% (${result.total_score}/${result.full_score}分)`}
            subTitle={`评级：${result.grade}`}
          />
          <Progress percent={Math.round(result.correct_rate * 100)} />
          {transcript && (
            <>
              <Divider orientation="left">📜 听力原文</Divider>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<PlayCircleOutlined />} onClick={() => player.speak(transcript)}>重听原文</Button>
                {player.playState === "loading" && (
                  <Button size="small" disabled icon={<PauseCircleOutlined />}>生成语音中...</Button>
                )}
                {player.playState === "playing" && (
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
                )}
                {player.playState === "paused" && (
                  <Button size="small" icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
                )}
              </Space>
              {player.playState === "loading" && (
                <div style={{ marginBottom: 8 }}>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} />} size="small" />
                  {" "}<Text type="secondary">正在生成语音，请稍候...</Text>
                </div>
              )}
              {(player.playState === "playing" || player.playState === "paused") && (
                <Progress percent={player.progress} size="small" style={{ marginBottom: 8 }} />
              )}
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 12, borderRadius: 6 }}>
                {transcript}
              </Paragraph>
            </>
          )}
          {result.details?.length > 0 && (
            <>
              <Divider orientation="left">📝 逐题对错</Divider>
              <ListeningDetailList
                details={result.details}
                showDialogue={meta.question_type === "短对话"}
                onPlayDialogue={(text) => player.speakDialogue(text)}
              />
            </>
          )}
          <Button style={{ marginTop: 16 }} onClick={reset}>
            重新生成
          </Button>
        </Card>
      )}

      <OralRecordsList category="英语听力" refreshKey={refreshKey} title="📄 听力记录" showFilters active={active} onDelete={handleDelete} />
    </div>
  );
}

/** 单词听写面板
 *
 * 与英语听力一致：选好筛选范围 → 生成听写任务后，AI 返回一段“老师口语化播报”
 * 文本（broadcast_text，中英夹杂）+ 答案词表（words，每词带 prompt_lang）。
 * 学生整段收听后作答，支持两种方式：
 * - 键盘输入：每行写一个答案，按播报顺序逐词匹配
 * - 上传图片：手写/拍照答案交多模态 AI 识别后批改
 */
function DictationPanel({ active }: { active?: boolean }) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [words, setWords] = useState<DictationWord[]>([]);
  const [result, setResult] = useState<DictationResult | null>(null);
  const [direction, setDirection] = useState("汉译英");
  const [difficulty, setDifficulty] = useState("中等");
  const [broadcastText, setBroadcastText] = useState("");
  const [wordScope, setWordScope] = useState("");
  const [answerMode, setAnswerMode] = useState<"keyboard" | "upload">("keyboard");
  const [keyboardAnswers, setKeyboardAnswers] = useState<string[]>([]);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const player = useTranscriptPlayer("dictation", 0.8);

  /** 播放结束后由 Hook 管理状态，这里无需额外处理 */

  const reset = useCallback(() => {
    player.stop();
    setWords([]);
    setResult(null);
    setBroadcastText("");
    setKeyboardAnswers([]);
    setUploadFile(null);
  }, [player.stop]);

  const generate = useCallback(async () => {
    const vals = await form.validateFields().catch(() => null);
    if (!vals) return;
    player.stop();
    setLoading(true);
    setResult(null);
    setKeyboardAnswers([]);
    setUploadFile(null);
    setBroadcastText("");
    try {
      const data = await oralService.generateDictation({
        word_scope: vals.word_scope,
        word_count: vals.word_count,
        direction,
        difficulty,
      });
      setWords(data.words || []);
      setKeyboardAnswers(new Array((data.words || []).length).fill(""));
      setBroadcastText(data.broadcast_text || "");
      setWordScope(vals.word_scope || "");
      message.success(`已生成含 ${data.words?.length || 0} 个单词的听写任务`);
    } catch {
      message.error("生成听写任务失败");
    } finally {
      setLoading(false);
    }
  }, [form, direction, difficulty, player.stop]);

  const submit = useCallback(async () => {
    if (words.length === 0) return;
    player.stop();
    const extra = { direction, broadcast_text: broadcastText, word_scope: wordScope, difficulty };
    setSubmitting(true);
    try {
      let data: DictationResult;
      if (answerMode === "upload") {
        if (!uploadFile) {
          message.warning("请先上传作答图片");
          setSubmitting(false);
          return;
        }
        data = await oralService.submitDictationImage(words, uploadFile, extra);
      } else {
        // 键盘输入：每道题一个切块，按题号与词表逐词对应（未填的切块视为空，提交时按答错计）
        const userSpellings = words.map((_, i) => (keyboardAnswers[i] || "").trim());
        data = await oralService.submitDictation(words, userSpellings, extra);
      }
      if (data.error) {
        message.error(data.error);
        return;
      }
      setResult(data);
      setRefreshKey((k) => k + 1);
    } catch {
      message.error("提交结果失败");
    } finally {
      setSubmitting(false);
    }
  }, [words, keyboardAnswers, uploadFile, answerMode, direction, difficulty, broadcastText, wordScope, player.stop]);

  /** 切换方向时重置 */
  const handleDirectionChange = useCallback((val: string) => {
    setDirection(val);
    reset();
  }, [reset]);

  const dirHint =
    direction === "英译汉"
      ? "老师会朗读英文单词，请写出对应的中文释义"
      : direction === "默写单词"
      ? "老师会朗读英文单词，请默写出同一个英文单词"
      : direction === "中英混合"
      ? "老师播报中英夹杂：部分要求写英文、部分要求写中文，请按播报提示作答"
      : "老师会报中文释义，请写出对应的英文单词";

  return (
    <div>
      <Card>
        <Form form={form} layout="inline" initialValues={{ word_scope: "小学必备词汇", word_count: 10 }}>
          <Form.Item label="测试方向">
            <Select
              value={direction}
              onChange={handleDirectionChange}
              options={[
                { label: "汉译英", value: "汉译英" },
                { label: "英译汉", value: "英译汉" },
                { label: "默写单词", value: "默写单词" },
                { label: "中英混合", value: "中英混合" },
              ]}
              style={{ width: 110 }}
            />
          </Form.Item>
          <Form.Item label="难度">
            <Select
              value={difficulty}
              onChange={setDifficulty}
              options={[
                { label: "简单", value: "简单" },
                { label: "中等", value: "中等" },
                { label: "困难", value: "困难" },
              ]}
              style={{ width: 90 }}
            />
          </Form.Item>
          <Form.Item label="词库范围" name="word_scope">
            <Select options={[
              { label: "小学必备词汇", value: "小学必备词汇" },
              { label: "初中必备词汇", value: "初中必备词汇" },
              { label: "高中必备词汇", value: "高中必备词汇" },
              { label: "四级词汇", value: "四级词汇" },
            ]} style={{ width: 140 }} />
          </Form.Item>
          <Form.Item label="单词数量" name="word_count">
            <InputNumber min={5} max={30} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={loading} onClick={generate} icon={<EditOutlined />}>
              生成听写任务
            </Button>
          </Form.Item>
          <Form.Item label="播放速度">
            <PlaybackRateControl value={player.playbackRate} onChange={player.setPlaybackRate} />
          </Form.Item>
        </Form>
      </Card>

      {loading && <div style={{ textAlign: "center", padding: 40 }}><Spin tip="AI 正在生成听写任务..." /></div>}

      {words.length > 0 && !result && (
        <Card style={{ marginTop: 16 }} title="🎧 单词听写作答">
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={`请点击下方按钮播放老师的听写播报，${dirHint}（作答阶段不显示原文）。本次共 ${words.length} 个单词。`}
          />
          <Space style={{ marginBottom: 8 }}>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => player.speak(broadcastText, "mixed")}>
              播放听写任务
            </Button>
            {player.playState === "loading" && (
              <Button disabled icon={<PauseCircleOutlined />}>生成语音中...</Button>
            )}
            {player.playState === "playing" && (
              <Button icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
            )}
            {player.playState === "paused" && (
              <Button icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
            )}
          </Space>
          {player.playState === "loading" && (
            <div style={{ marginBottom: 16 }}>
              <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} />} size="small" />
              {" "}<Text type="secondary">正在生成语音，请稍候...</Text>
            </div>
          )}
          {(player.playState === "playing" || player.playState === "paused") && (
            <Progress percent={player.progress} size="small" style={{ marginBottom: 16 }} />
          )}

          <Divider orientation="left">✍️ 作答方式</Divider>
          <Radio.Group
            value={answerMode}
            onChange={(e) => setAnswerMode(e.target.value)}
            style={{ marginBottom: 16 }}
          >
            <Radio.Button value="keyboard">键盘输入</Radio.Button>
            <Radio.Button value="upload">上传图片</Radio.Button>
          </Radio.Group>

          {answerMode === "keyboard" && (
            <div>
              <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                请将每个答案填入对应题号的输入框；可以留空，留空的题目提交时按答错计。
              </Text>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
                {words.map((_, i) => (
                  <Input
                    key={i}
                    addonBefore={`第 ${i + 1} 题`}
                    value={keyboardAnswers[i] || ""}
                    onChange={(e) => {
                      const val = e.target.value;
                      setKeyboardAnswers((prev) => {
                        const next = prev.length === words.length ? [...prev] : new Array(words.length).fill("");
                        next[i] = val;
                        return next;
                      });
                    }}
                  />
                ))}
              </div>
            </div>
          )}

          {answerMode === "upload" && (
            <Upload.Dragger
              accept="image/*"
              maxCount={1}
              multiple={false}
              beforeUpload={(file) => {
                setUploadFile(file as unknown as File);
                return false; // 阻止自动上传，提交时统一发送
              }}
              onRemove={() => setUploadFile(null)}
              fileList={uploadFile ? [{ uid: "-1", name: uploadFile.name, status: "done" as const }] : []}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽图片到此处上传手写作答</p>
              <p className="ant-upload-hint">支持拍照或截图，AI 将识别图中作答内容后批改</p>
            </Upload.Dragger>
          )}

          <Button type="primary" block size="large" loading={submitting} onClick={submit} style={{ marginTop: 16 }}>
            提交批改
          </Button>
        </Card>
      )}

      {result && (
        <Card style={{ marginTop: 16 }} title="📊 批改结果">
          <Result
            status={result.total > 0 && result.correct_count / result.total >= 0.7 ? "success" : "info"}
            title={`正确：${result.correct_count}/${result.total}`}
          />
          {result.wrong_words?.length > 0 && (
            <>
              <Divider>错误单词</Divider>
              <List
                dataSource={result.wrong_words}
                renderItem={(w) => (
                  <List.Item>
                    <Space wrap>
                      {w.prompt_lang && <Tag color="purple">{w.prompt_lang}</Tag>}
                      <Text delete type="danger">{w.user_spelling || "(空)"}</Text>
                      <Text>→</Text>
                      <Text strong type="success">{w.correct_spelling}</Text>
                      <Tag>{w.word}{w.chinese ? ` / ${w.chinese}` : ""}</Tag>
                    </Space>
                  </List.Item>
                )}
              />
            </>
          )}
          {broadcastText && (
            <>
              <Divider orientation="left">📜 听写播报原文</Divider>
              <Space style={{ marginBottom: 8 }}>
                <Button size="small" icon={<PlayCircleOutlined />} onClick={() => player.speak(broadcastText, "mixed")}>重听播报</Button>
                {player.playState === "playing" && (
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={player.pause}>暂停</Button>
                )}
                {player.playState === "paused" && (
                  <Button size="small" icon={<CaretRightOutlined />} onClick={player.resume}>继续</Button>
                )}
              </Space>
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 12, borderRadius: 6 }}>
                {broadcastText}
              </Paragraph>
            </>
          )}
          <Button style={{ marginTop: 16 }} onClick={reset}>
            重新生成
          </Button>
        </Card>
      )}

      <OralRecordsList
        category="单词听写"
        refreshKey={refreshKey}
        title="🎧 单词听写记录"
        showDictationFilters
        active={active}
        onDelete={(id) => oralService.deleteRecord(id)}
      />
    </div>
  );
}

/** 普通话测评面板
 *
 * 流程：AI 生成朗读文本 → 用户录音朗读 → 讯飞流式语音评测（read_chapter 篇章朗读）
 * 打分（声韵/声调/流畅度/完整度，百分制）→ AI 基于评测数据生成评语与建议。
 *
 * 每条评测记录保存：测评等级、参考文本、用户音频、维度得分、AI评语，支持删除。
 */
function MandarinPanel({ active }: { active?: boolean }) {
  const [level, setLevel] = useState("二级甲等");
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<MandarinResult | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // AI生成文本相关
  const [generatedText, setGeneratedText] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("中等");
  const [textLength, setTextLength] = useState("短");

  // 音频录制
  const recorder = useAudioRecorder();

  /** 生成朗读文本 */
  const generateText = useCallback(async () => {
    setGenerating(true);
    try {
      const data = await oralService.generateMandarinText({
        topic, difficulty, length: textLength,
      });
      if (data.text) {
        setGeneratedText(data.text);
        message.success("朗读文本已生成");
        recorder.reset();
        setResult(null);
      } else {
        message.error(data.error || "生成文本失败");
      }
    } catch {
      message.error("生成朗读文本失败");
    } finally {
      setGenerating(false);
    }
  }, [topic, difficulty, textLength, recorder]);

  /** 提交评测（讯飞流式语音评测） */
  const submitEvaluation = useCallback(async () => {
    if (!generatedText.trim()) {
      message.warning("请先生成朗读文本");
      return;
    }
    if (!recorder.audioBlob) {
      message.warning("请先录制音频");
      return;
    }
    setLoading(true);
    try {
      // 讯飞语音评测要求 16k 采样率 / 16bit / 单声道，先在浏览器端转成 WAV
      let wavBlob: Blob;
      try {
        wavBlob = await blobToWav16k(recorder.audioBlob);
      } catch {
        message.error("录音转码失败，请使用 Chrome / Edge 浏览器重新录制");
        return;
      }
      const formData = new FormData();
      formData.append("audio", wavBlob, "recording.wav");
      formData.append("test_level", level);
      formData.append("text_content", generatedText);
      const data = await oralService.evaluateMandarinAudio(formData);
      setResult(data);
      setRefreshKey((k) => k + 1);
      message.success("评测完成");
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "语音评测失败，请重试");
    } finally {
      setLoading(false);
    }
  }, [recorder.audioBlob, level, generatedText]);

  /** 重新开始 */
  const handleReset = useCallback(() => {
    setResult(null);
    recorder.reset();
    setGeneratedText("");
  }, [recorder]);

  /** 删除记录回调 */
  const handleDelete = useCallback(async (id: number) => {
    await oralService.deleteRecord(id);
  }, []);

  // 各维度中文名映射（讯飞评测：声韵/声调/流畅度/完整度）
  const dimNameMap: Record<string, string> = {
    pronunciation: "声韵准确度",
    tone: "声调准确度",
    fluency: "流畅度",
    completeness: "完整度",
  };

  // 讯飞评测各维度为百分制
  const dimFull = result?.dimension_full_score ?? 100;

  /** 格式化录音时长 */
  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div>
      {/* 等级选择 + 朗读文本生成条件（同行） */}
      <Card>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Space wrap size="middle">
            <Space>
              <Text strong>测评等级：</Text>
              <Select
                value={level}
                onChange={setLevel}
                options={[
                  { label: "一级甲等", value: "一级甲等" },
                  { label: "一级乙等", value: "一级乙等" },
                  { label: "二级甲等", value: "二级甲等" },
                  { label: "二级乙等", value: "二级乙等" },
                  { label: "三级甲等", value: "三级甲等" },
                ]}
                style={{ width: 130 }}
              />
            </Space>
          </Space>

          {/* 朗读文本生成：话题/难度/长度 + 生成按钮 */}
          {!generatedText && (
            <Space wrap size="small">
              <Input
                placeholder="话题（可选）"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                style={{ width: 130 }}
                allowClear
              />
              <Select
                value={difficulty}
                onChange={setDifficulty}
                options={[
                  { label: "简单", value: "简单" },
                  { label: "中等", value: "中等" },
                  { label: "困难", value: "困难" },
                ]}
                style={{ width: 90 }}
              />
              <Select
                value={textLength}
                onChange={setTextLength}
                options={[
                  { label: "短", value: "短" },
                  { label: "中", value: "中" },
                  { label: "长", value: "长" },
                ]}
                style={{ width: 80 }}
              />
              <Button type="primary" loading={generating} onClick={generateText} icon={<SoundOutlined />}>
                生成朗读文本
              </Button>
            </Space>
          )}
        </Space>
      </Card>

      {/* 生成中提示：LLM 生成朗读文本通常需要 10~30 秒，期间无任何页面变化，
          若不加提示用户会误以为按钮无反应，故生成时显示明确进度信息 */}
      {generating && (
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 16 }}
          message="AI 正在生成朗读文本，通常需要 10~30 秒，请耐心等待..."
        />
      )}

      {/* 朗读文本展示 */}
      {generatedText && (
        <Card style={{ marginTop: 16 }} title="📖 朗读文本">
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="请朗读以下文本，朗读完毕后点击停止录音并提交评测"
          />
          <Paragraph
            style={{
              whiteSpace: "pre-wrap",
              background: "#fafafa",
              padding: 16,
              borderRadius: 8,
              fontSize: 16,
              lineHeight: 2,
              border: "1px solid #f0f0f0",
            }}
          >
            {generatedText}
          </Paragraph>
          <Space style={{ marginTop: 8 }}>
            <Button size="small" onClick={() => setGeneratedText("")}>重新生成</Button>
          </Space>
        </Card>
      )}

      {/* 录音区域（已生成朗读文本且未提交结果时显示） */}
      {generatedText && !result && (
        <Card style={{ marginTop: 16 }} title="🎙️ 录音">
          <div style={{ textAlign: "center", padding: "20px 0" }}>
            {!recorder.audioUrl ? (
              <>
                {/* 录音按钮 */}
                {!recorder.recording ? (
                  <Button
                    type="primary"
                    size="large"
                    icon={<CustomerServiceOutlined />}
                    onClick={recorder.startRecording}
                    style={{ width: 120, height: 120, borderRadius: "50%", fontSize: 18 }}
                  >
                    开始录音
                  </Button>
                ) : (
                  <Button
                    danger
                    size="large"
                    icon={<PauseCircleOutlined />}
                    onClick={recorder.stopRecording}
                    style={{ width: 120, height: 120, borderRadius: "50%", fontSize: 18 }}
                  >
                    停止录音
                  </Button>
                )}
                {recorder.recording && (
                  <div style={{ marginTop: 16 }}>
                    <Text type="secondary" style={{ fontSize: 24, fontFamily: "monospace" }}>
                      {formatTime(recorder.recordingTime)}
                    </Text>
                    <br />
                    <Tag color="red" style={{ marginTop: 8 }}>录音中...</Tag>
                  </div>
                )}
                {!recorder.recording && !recorder.audioUrl && (
                  <div style={{ marginTop: 12 }}>
                    <Text type="secondary">点击按钮开始录制普通话朗读</Text>
                  </div>
                )}
              </>
            ) : (
              <>
                {/* 录音完成，可播放 */}
                <Text strong style={{ fontSize: 16 }}>录音完成 ✓</Text>
                <br />
                <audio controls src={recorder.audioUrl} style={{ marginTop: 12, width: "100%", maxWidth: 400 }} />
                <br />
                <Space style={{ marginTop: 12 }}>
                  <Button onClick={recorder.reset}>重新录制</Button>
                  <Button type="primary" loading={loading} onClick={submitEvaluation} icon={<CheckCircleOutlined />}>
                    提交评测
                  </Button>
                </Space>
              </>
            )}
          </div>
        </Card>
      )}

      {/* 加载中 */}
      {loading && (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin tip="讯飞语音评测中，请稍候..." />
        </div>
      )}

      {/* 评测结果 */}
      {result && (
        <Card style={{ marginTop: 16 }} title="📊 测评结果">
          <Result
            status={result.total_score >= 70 ? "success" : "info"}
            title={`总分：${result.total_score}分`}
            subTitle={`评级：${result.level || level}`}
          />

          {/* 维度得分 */}
          {result.dimension_scores && Object.keys(result.dimension_scores).length > 0 && (
            <DimensionScoreBlock
              scores={result.dimension_scores}
              fullScore={dimFull}
              nameMap={dimNameMap}
              dividerText="各维度得分"
            />
          )}

          {/* 乱读提示（讯飞评测） */}
          {result.is_rejected && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message="检测到朗读内容与参考文本严重不符，成绩可能不准确，请对照文本重新朗读。"
            />
          )}

          {/* 朗读有误的字词（讯飞评测） */}
          {result.error_chars && result.error_chars.length > 0 && (
            <>
              <Divider>❌ 朗读有误的字词</Divider>
              <Space wrap>
                {result.error_chars.map((c, i) => <Tag key={i} color="red">{c}</Tag>)}
              </Space>
            </>
          )}

          {/* 转写文本 */}
          {result.transcribed_text && (
            <>
              <Divider>📝 语音转写</Divider>
              <Paragraph style={{ background: "#fafafa", padding: 12, borderRadius: 6, whiteSpace: "pre-wrap" }}>
                {result.transcribed_text}
              </Paragraph>
            </>
          )}

          {/* AI 综合评语 */}
          {result.ai_comment && (
            <>
              <Divider>🤖 AI 评语</Divider>
              <Alert type="info" message={result.ai_comment} />
            </>
          )}

          {/* 改进建议 */}
          {result.suggestions && result.suggestions.length > 0 && (
            <>
              <Divider>💡 改进建议</Divider>
              <List
                size="small"
                dataSource={result.suggestions}
                renderItem={(s, i) => (
                  <List.Item>
                    <Text>{i + 1}. {typeof s === "string" ? s : JSON.stringify(s)}</Text>
                  </List.Item>
                )}
              />
            </>
          )}

          {/* 录音回放 */}
          {recorder.audioUrl && (
            <>
              <Divider>🔊 录音回放</Divider>
              <audio controls src={recorder.audioUrl} style={{ width: "100%", maxWidth: 400 }} />
            </>
          )}

          <div style={{ marginTop: 16 }}>
            <Button onClick={handleReset}>重新测评</Button>
          </div>
        </Card>
      )}

      {/* 普通话测评记录 */}
      <OralRecordsList
        category="普通话测评"
        refreshKey={refreshKey}
        title="📄 普通话测评记录"
        showMandarinDetail
        active={active}
        onDelete={handleDelete}
      />
    </div>
  );
}

/** 子板块切换键：listening=英语听力 / dictation=单词听写 / mandarin=普通话测评 */
type OralTabKey = "listening" | "dictation" | "mandarin";

/** 听力与口语主页 */
export default function OralAssessmentPage() {
  /** 当前子板块（默认英语听力），用大号分段切换器控制（与作文批改一致） */
  const [tab, setTab] = useState<OralTabKey>("listening");

  return (
    <div style={{ padding: "12px 0 24px", maxWidth: 1280, margin: "0 auto" }}>
      {/* 页面头部：无 emoji，纯排版层次 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ marginBottom: 0, letterSpacing: "-0.02em" }}>
          听力与口语
        </Title>
      </div>

      {/* 大号分段切换：激活项深蓝实底白字（样式见 soft-ui.css 8.1 节，与作文批改语文/英语切换一致） */}
      <Segmented
        block
        className="soft-section-switcher"
        value={tab}
        onChange={(v) => setTab(v as OralTabKey)}
        options={[
          { value: "listening", label: "英语听力", icon: <SoundOutlined /> },
          { value: "dictation", label: "单词听写", icon: <EditOutlined /> },
          { value: "mandarin", label: "普通话测评", icon: <CustomerServiceOutlined /> },
        ]}
      />

      {/* 三个子板块常驻挂载（隐藏而非卸载），切换时保留各自的状态；
           active 传给面板控制 OralRecordsList 是否加载，未激活的 tab 不发查询请求 */}
      <div hidden={tab !== "listening"} style={{ marginTop: 24 }}>
        <ListeningPanel active={tab === "listening"} />
      </div>
      <div hidden={tab !== "dictation"} style={{ marginTop: 24 }}>
        <DictationPanel active={tab === "dictation"} />
      </div>
      <div hidden={tab !== "mandarin"} style={{ marginTop: 24 }}>
        <MandarinPanel active={tab === "mandarin"} />
      </div>
    </div>
  );
}
