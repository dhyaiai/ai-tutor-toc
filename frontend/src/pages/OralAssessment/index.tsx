/**
 * 听力与口语页面
 *
 * 3 个子模块标签页：
 * - 英语听力：AI 生成听力试卷 → 在线作答 → 自动批改
 * - 单词听写：选择词库范围 → 播放听写 → 提交批改
 * - 普通话测评：输入/朗读文段 → AI 评分反馈
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Tabs, Card, Form, Select, InputNumber, Button, Input, Typography,
  List, Tag, Spin, message, Result, Empty, Radio, Divider, Space, Progress, Modal, Alert,
  Popconfirm, Table, Upload,
} from "antd";
import {
  SoundOutlined, EditOutlined, CustomerServiceOutlined,
  PlayCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, PauseCircleOutlined,
  CaretRightOutlined, LoadingOutlined, InboxOutlined, DeleteOutlined,
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
  type MandarinMode,
} from "../../services/oralService";
import { formatDate } from "../../utils/helpers";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

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

/** 播放速度持久化 localStorage key 前缀（听力/听写各自记忆） */
const SPEED_STORAGE_PREFIX = "oral_playback_rate_";

/** 读取已保存的播放速度（无效或未保存时返回默认值） */
function loadStoredRate(storageKey: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(SPEED_STORAGE_PREFIX + storageKey);
    const v = raw == null ? NaN : parseFloat(raw);
    if (isFinite(v) && v >= 0.75 && v <= 1.5) return v;
  } catch { /* ignore */ }
  return fallback;
}

/** 播放速度输入框（0.75–1.5，步长 0.05，与题数筛选项同为 InputNumber 风格；播放中调整即时生效） */
function SpeedSelect({
  value, onChange, size = "middle",
}: { value: number; onChange: (v: number) => void; size?: "small" | "middle" }) {
  return (
    <InputNumber
      size={size}
      min={0.75}
      max={1.5}
      step={0.05}
      value={value}
      onChange={(v) => { if (typeof v === "number") onChange(Math.round(v * 100) / 100); }}
      title="播放速度"
    />
  );
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
  const initialRate = loadStoredRate(storageKey, defaultRate);
  const [playState, setPlayState] = useState<"idle" | "loading" | "playing" | "paused">("idle");
  const [progress, setProgress] = useState(0);
  const [playbackRate, setPlaybackRateState] = useState(initialRate);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const stoppedRef = useRef(false);
  const playbackRateRef = useRef(initialRate);
  const timerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  /** 调整播放速度（播放中即时生效，并持久化保存，后续播放沿用） */
  const setPlaybackRate = (rate: number) => {
    playbackRateRef.current = rate;
    setPlaybackRateState(rate);
    if (audioRef.current) audioRef.current.playbackRate = rate;
    try { localStorage.setItem(SPEED_STORAGE_PREFIX + storageKey, String(rate)); } catch { /* ignore */ }
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
   *  voice 可选：default(英语女声)/mixed(中文女声，可读中英混合的单词听写播报)
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
    const fetchUrl = `/api/v1/oral/tts?text=${encodeURIComponent(cleaned)}&rate=${encodeURIComponent("+0%")}&voice=${encodeURIComponent(voice)}`;
    const controller = new AbortController();
    abortRef.current = controller;

    // 用 fetch 下载音频 Blob（可检测 HTTP 错误、支持 AbortController）
    fetch(fetchUrl, { signal: controller.signal })
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

    fetch(fetchUrl, { signal: controller.signal })
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
        // 清理旧 URL
        if (audioUrl) URL.revokeObjectURL(audioUrl);
        setAudioUrl(URL.createObjectURL(blob));
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
    if (audioUrl) { URL.revokeObjectURL(audioUrl); setAudioUrl(null); }
    setAudioBlob(null);
  }, [audioUrl, clearTimer]);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      clearTimer();
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
      if (audioUrl) URL.revokeObjectURL(audioUrl);
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

  useEffect(() => {
    if (!open || recordId == null) return;
    player.stop();
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then(setRecord)
      .catch(() => message.error("加载记录详情失败"))
      .finally(() => setLoading(false));
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
                <SpeedSelect value={player.playbackRate} onChange={player.setPlaybackRate} size="small" />
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

  useEffect(() => {
    if (!open || recordId == null) return;
    player.stop();
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then(setRecord)
      .catch(() => message.error("加载记录详情失败"))
      .finally(() => setLoading(false));
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
                <SpeedSelect value={player.playbackRate} onChange={player.setPlaybackRate} size="small" />
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
 * 英语听力记录(showFilters=true)：表头模式，含学段/题型筛选，点击名称或「查看详情」可查看听力原文/题目/作答/对错。
 * 单词听写记录(showDictationFilters=true)：表头模式，含测试方向/词库范围筛选，点击名称或「查看详情」可查看听力原文/题目与作答。
 * 普通话测评记录(showMandarinDetail=true)：可点击查看测评等级、音频、转写文本、AI评语。
 * 其他类别：简单 List 模式。
 * 每条记录末尾有删除按钮。
 */
function OralRecordsList({
  category, refreshKey, title, showMandarinDetail, showFilters, showDictationFilters, onDelete,
}: {
  category: string; refreshKey: number;
  title?: string;
  showMandarinDetail?: boolean;
  showFilters?: boolean;
  showDictationFilters?: boolean;
  onDelete?: (id: number) => Promise<void>;
}) {
  const [records, setRecords] = useState<OralRecord[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeMandarinId, setActiveMandarinId] = useState<number | null>(null);
  const [activeDictationId, setActiveDictationId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
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

  useEffect(() => { load(); }, [load, refreshKey]);

  /** 处理删除：调用父组件回调，成功后刷新列表 */
  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation();
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

  /** Table 列定义（英语听力表头模式） */
  const columns = [
    {
      title: "听力名称",
      dataIndex: "name",
      key: "name",
      width: 220,
      ellipsis: true,
      render: (_: string, r: OralRecord) => (
        <a onClick={() => setActiveId(r.id)}>{r.name}</a>
      ),
    },
    {
      title: "学段",
      dataIndex: "grade_level",
      key: "grade_level",
      width: 80,
      render: (v: string | null) => v ? <Tag color="purple">{v}</Tag> : "-",
    },
    {
      title: "题型",
      dataIndex: "question_type",
      key: "question_type",
      width: 100,
      render: (v: string) => v ? <Tag color="cyan">{v}</Tag> : "-",
    },
    {
      title: "分值",
      dataIndex: "score",
      key: "score",
      width: 90,
      render: (v: string | null) => v ? <Tag color="blue">{v}</Tag> : "-",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 130,
      render: (v: string) => <Text type="secondary">{formatDate(v)}</Text>,
    },
    {
      title: "操作",
      key: "actions",
      width: 190,
      render: (_: unknown, r: OralRecord) => (
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          onClick={(e) => e.stopPropagation()}
        >
          <a onClick={() => setActiveId(r.id)}>查看详情</a>
          <Popconfirm
            title="确定删除该记录？"
            description="删除后不可恢复"
            onConfirm={(e) => handleDelete(r.id, e as unknown as React.MouseEvent)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />} loading={deleting === r.id} onClick={(e) => e.stopPropagation()} />
          </Popconfirm>
        </div>
      ),
    },
  ];

  /** Table 列定义（单词听写表头模式） */
  const dictationColumns = [
    {
      title: "作业名称",
      dataIndex: "name",
      key: "name",
      width: 200,
      ellipsis: true,
      render: (_: string, r: OralRecord) => (
        <a onClick={() => setActiveDictationId(r.id)}>{r.name}</a>
      ),
    },
    {
      title: "分值（得分/总分）",
      dataIndex: "score",
      key: "score",
      width: 120,
      render: (v: string | null) => v ? <Tag color="blue">{v}</Tag> : "-",
    },
    {
      title: "词库范围",
      dataIndex: "word_scope",
      key: "word_scope",
      width: 130,
      render: (v: string) => v ? <Tag color="geekblue">{v}</Tag> : "-",
    },
    {
      title: "测试方向",
      dataIndex: "direction",
      key: "direction",
      width: 110,
      render: (v: string) => v ? <Tag color="orange">{v}</Tag> : "-",
    },
    {
      title: "难度",
      dataIndex: "difficulty",
      key: "difficulty",
      width: 90,
      render: (v: string) => {
        if (!v) return "-";
        const color = v === "困难" ? "red" : v === "简单" ? "green" : "gold";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    {
      title: "听写时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 130,
      render: (v: string) => <Text type="secondary">{formatDate(v)}</Text>,
    },
    {
      title: "查看详情",
      key: "actions",
      width: 160,
      render: (_: unknown, r: OralRecord) => (
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          onClick={(e) => e.stopPropagation()}
        >
          <a onClick={() => setActiveDictationId(r.id)}>查看详情</a>
          <Popconfirm
            title="确定删除该记录？"
            description="删除后不可恢复"
            onConfirm={(e) => handleDelete(r.id, e as unknown as React.MouseEvent)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />} loading={deleting === r.id} onClick={(e) => e.stopPropagation()} />
          </Popconfirm>
        </div>
      ),
    },
  ];

  return (
    <Card style={{ marginTop: 16 }} title={title || "📄 作业记录"}>
      {/* 筛选栏（仅英语听力模式） */}
      {showFilters && (
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            allowClear
            placeholder="学段"
            style={{ width: 120 }}
            value={filterGrade}
            onChange={(v) => setFilterGrade(v)}
            options={[
              { label: "小学", value: "小学" },
              { label: "初中", value: "初中" },
              { label: "高中", value: "高中" },
            ]}
          />
          <Select
            allowClear
            placeholder="题型"
            style={{ width: 140 }}
            value={filterType}
            onChange={(v) => setFilterType(v)}
            options={[
              { label: "短对话", value: "短对话" },
              { label: "长对话", value: "长对话" },
              { label: "短文理解", value: "短文理解" },
            ]}
          />
        </Space>
      )}
      {/* 筛选栏（仅单词听写模式） */}
      {showDictationFilters && (
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            allowClear
            placeholder="测试方向"
            style={{ width: 130 }}
            value={filterDirection}
            onChange={(v) => setFilterDirection(v)}
            options={[
              { label: "汉译英", value: "汉译英" },
              { label: "英译汉", value: "英译汉" },
              { label: "默写单词", value: "默写单词" },
              { label: "中英混合", value: "中英混合" },
            ]}
          />
          <Select
            allowClear
            placeholder="难度"
            style={{ width: 110 }}
            value={filterDifficulty}
            onChange={(v) => setFilterDifficulty(v)}
            options={[
              { label: "简单", value: "简单" },
              { label: "中等", value: "中等" },
              { label: "困难", value: "困难" },
            ]}
          />
          <Select
            allowClear
            placeholder="词库范围"
            style={{ width: 150 }}
            value={filterScope}
            onChange={(v) => setFilterScope(v)}
            options={[
              { label: "小学必备词汇", value: "小学必备词汇" },
              { label: "初中必备词汇", value: "初中必备词汇" },
              { label: "高中必备词汇", value: "高中必备词汇" },
              { label: "四级词汇", value: "四级词汇" },
            ]}
          />
        </Space>
      )}
      {records.length === 0 ? (
        <Empty description="暂无记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : showFilters ? (
        /* 英语听力：Table 表头模式（仅名称/查看详情可打开详情） */
        <Table
          columns={columns}
          dataSource={records}
          rowKey="id"
          size="middle"
          pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
        />
      ) : showDictationFilters ? (
        /* 单词听写：Table 表头模式（仅名称/查看详情可打开详情） */
        <Table
          columns={dictationColumns}
          dataSource={records}
          rowKey="id"
          size="middle"
          pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
        />
      ) : (
        /* 其他类别：简单 List 模式 */
        <List
          dataSource={records}
          renderItem={(r) => (
            <List.Item
              style={clickable || mandarinClickable ? { cursor: "pointer" } : undefined}
              onClick={() => {
                if (clickable) setActiveId(r.id);
                if (mandarinClickable) setActiveMandarinId(r.id);
              }}
              actions={[
                (clickable || mandarinClickable) ? (
                  <a key="view" onClick={(e) => {
                    e.stopPropagation();
                    if (clickable) setActiveId(r.id);
                    if (mandarinClickable) setActiveMandarinId(r.id);
                  }}>
                    查看详情
                  </a>
                ) : null,
                <Popconfirm
                  key="delete"
                  title="确定删除该记录？"
                  description="删除后不可恢复"
                  onConfirm={(e) => handleDelete(r.id, e as unknown as React.MouseEvent)}
                  okText="确定"
                  cancelText="取消"
                >
                  <Button
                    size="small"
                    type="link"
                    danger
                    icon={<DeleteOutlined />}
                    loading={deleting === r.id}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>,
              ].filter(Boolean)}
            >
              <Space wrap>
                <Text strong>{r.name}</Text>
                {r.grade_level && <Tag color="purple">{r.grade_level}</Tag>}
                {r.score && <Tag color="blue">{r.score}</Tag>}
                <Text type="secondary">{formatDate(r.created_at)}</Text>
              </Space>
            </List.Item>
          )}
        />
      )}
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
    </Card>
  );
}

/** 普通话测评记录详情弹窗（展示等级、音频、转写文本、AI评语） */
function MandarinRecordDetailModal({
  recordId, open, onClose,
}: { recordId: number | null; open: boolean; onClose: () => void }) {
  const [loading, setLoading] = useState(false);
  const [record, setRecord] = useState<OralRecordDetail | null>(null);

  useEffect(() => {
    if (!open || recordId == null) return;
    setLoading(true);
    setRecord(null);
    oralService.getRecordDetail(recordId)
      .then(setRecord)
      .catch(() => message.error("加载记录详情失败"))
      .finally(() => setLoading(false));
  }, [open, recordId]);

  const detail = record?.detail || {};
  const dimNameMap: Record<string, string> = {
    pronunciation: "语音标准度",
    grammar: "词汇语法规范度",
    fluency: "流畅度",
    completeness: "内容完整度",
    intonation: "语调自然度",
  };

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
          </Space>

          {/* 维度得分 */}
          {detail.dimension_scores && typeof detail.dimension_scores === "object" && Object.keys(detail.dimension_scores).length > 0 && (
            <>
              <Divider orientation="left">📊 维度得分</Divider>
              {Object.entries(detail.dimension_scores as Record<string, unknown>).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 8 }}>
                  <Space>
                    <Text>{dimNameMap[k] || k}</Text>
                    <Text strong>{String(v)}/25</Text>
                  </Space>
                  <Progress
                    percent={Math.round((Number(v) / 25) * 100)}
                    size="small"
                    status={Number(v) >= 18 ? "success" : Number(v) >= 12 ? "normal" : "exception"}
                  />
                </div>
              ))}
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

          {/* 音频回放 */}
          {detail.audio_url ? (
            <>
              <Divider orientation="left">🔊 录音回放</Divider>
              <audio
                controls
                src={`/api/v1/files/${String(detail.audio_url)}`}
                style={{ width: "100%" }}
              />
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
function ListeningPanel() {
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
        const response = await fetch(fetchUrl);
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
            <SpeedSelect value={player.playbackRate} onChange={player.setPlaybackRate} />
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

      <OralRecordsList category="英语听力" refreshKey={refreshKey} title="📄 听力记录" showFilters onDelete={handleDelete} />
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
function DictationPanel() {
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
            <SpeedSelect value={player.playbackRate} onChange={player.setPlaybackRate} />
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
        onDelete={(id) => oralService.deleteRecord(id)}
      />
    </div>
  );
}

/** 普通话测评面板
 *
 * 两种模式：
 * - AI生成文本：AI 生成朗读文本 → 用户录音朗读 → AI 对比原文评测发音
 * - 自行发挥：用户自行朗读一段内容 → AI 评测口语表达
 *
 * 每条评测记录保存：测评等级、用户音频、转写文本、AI评语，支持删除。
 */
function MandarinPanel() {
  const [mode, setMode] = useState<MandarinMode>("ai_generated");
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

  /** 提交评测 */
  const submitEvaluation = useCallback(async () => {
    if (!recorder.audioBlob) {
      message.warning("请先录制音频");
      return;
    }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("audio", recorder.audioBlob, "recording.webm");
      formData.append("evaluation_mode", mode);
      formData.append("test_level", level);
      formData.append("strict_level", "3");
      // AI生成文本模式：附带参考文本
      if (mode === "ai_generated" && generatedText) {
        formData.append("text_content", generatedText);
      }
      const data = await oralService.evaluateMandarinAudio(formData);
      setResult(data);
      setRefreshKey((k) => k + 1);
      if (data.error) {
        message.warning("评测部分完成：" + data.error);
      } else {
        message.success("评测完成");
      }
    } catch {
      message.error("评测失败，请重试");
    } finally {
      setLoading(false);
    }
  }, [recorder.audioBlob, mode, level, generatedText]);

  /** 切换模式时重置 */
  const handleModeChange = useCallback((newMode: MandarinMode) => {
    setMode(newMode);
    setResult(null);
    recorder.reset();
    if (newMode === "ai_generated") {
      // 保留已生成的文本，不需要重置
    }
  }, [recorder]);

  /** 重新开始 */
  const handleReset = useCallback(() => {
    setResult(null);
    recorder.reset();
    if (mode === "ai_generated") {
      setGeneratedText("");
    }
  }, [mode, recorder]);

  /** 删除记录回调 */
  const handleDelete = useCallback(async (id: number) => {
    await oralService.deleteRecord(id);
  }, []);

  // 各维度中文名映射
  const dimNameMap: Record<string, string> = {
    pronunciation: "语音标准度",
    grammar: "词汇语法规范度",
    fluency: "流畅度",
    completeness: "内容完整度",
    intonation: "语调自然度",
  };

  /** 格式化录音时长 */
  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div>
      {/* 模式选择 + 等级选择 + 筛选条件（同行） */}
      <Card>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Space wrap size="middle">
            <Space>
              <Text strong>评测模式：</Text>
              <Radio.Group
                value={mode}
                onChange={(e) => handleModeChange(e.target.value)}
                optionType="button"
                buttonStyle="solid"
              >
                <Radio.Button value="ai_generated">AI生成文本</Radio.Button>
                <Radio.Button value="free_speech">自行发挥</Radio.Button>
              </Radio.Group>
            </Space>
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

          {/* AI生成文本模式：话题/难度/长度 + 生成按钮 */}
          {mode === "ai_generated" && !generatedText && (
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

      {/* AI生成文本模式：显示生成的文本 */}
      {mode === "ai_generated" && generatedText && (
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

      {/* 自行发挥模式：提示 */}
      {mode === "free_speech" && !result && (
        <Card style={{ marginTop: 16 }}>
          <Alert
            type="info"
            showIcon
            message="自行发挥模式：请录制一段普通话口语内容（如自我介绍、话题演讲、日常对话等），AI 将对您的发音和表达进行评测。"
          />
        </Card>
      )}

      {/* 录音区域（未提交结果时显示） */}
      {!result && (
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
          <Spin tip="AI 正在进行语音评测，请耐心等待..." />
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
            <>
              <Divider>各维度得分</Divider>
              {Object.entries(result.dimension_scores).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 12 }}>
                  <Space>
                    <Text>{dimNameMap[k] || k}</Text>
                    <Text strong>{v}/25</Text>
                  </Space>
                  <Progress
                    percent={Math.round((Number(v) / 25) * 100)}
                    size="small"
                    status={Number(v) >= 18 ? "success" : Number(v) >= 12 ? "normal" : "exception"}
                  />
                </div>
              ))}
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
        onDelete={handleDelete}
      />
    </div>
  );
}

/** 听力与口语主页 */
export default function OralAssessmentPage() {
  const tabItems = [
    {
      key: "listening",
      label: <span><SoundOutlined /> 英语听力</span>,
      children: <ListeningPanel />,
    },
    {
      key: "dictation",
      label: <span><EditOutlined /> 单词听写</span>,
      children: <DictationPanel />,
    },
    {
      key: "mandarin",
      label: <span><CustomerServiceOutlined /> 普通话测评</span>,
      children: <MandarinPanel />,
    },
  ];

  return (
    <div style={{ padding: "24px 0" }}>
      <Title level={3}>🎧 听力与口语</Title>
      <Paragraph type="secondary">
        AI 驱动的听说训练：听力理解、单词听写、普通话测评，实时批改反馈。
      </Paragraph>
      <Tabs defaultActiveKey="listening" items={tabItems} size="large" />
    </div>
  );
}
