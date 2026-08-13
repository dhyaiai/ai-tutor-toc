/**
 * 助教讲解卡片组件
 *
 * 嵌入在题目卡片下方，流程：
 * 1. 点击"助教讲解" → 一次性生成完整讲解 + 一道思考题
 * 2. 展示完整讲解（支持 TTS 语音播报）与关联知识点
 * 3. 学生在下方输入框回答思考题，提交后由 AI 判定对错并给出反馈
 *    （判题结果同步记录到知识状态系统）
 *
 * 语音播报：后端 Edge TTS（/oral/tts，voice=mixed/mixed_male 中英混读，音色随助教设置）合成 MP3 播放，
 * 音频 Blob 按文本+音色缓存，重复播放不重复合成。
 * 仅在无公式科目（语文/英语/生物/政治/历史/地理）下挂载本组件，
 * 因此不需要公式→口语转写。
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { Button, Typography, Tag, Space, message, Spin, Input, Alert } from "antd";
import {
  MessageOutlined,
  SoundOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  LoadingOutlined,
  SendOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import {
  explainExercise,
  checkThinkingAnswer,
  recordFeedback,
  type ExplainResult,
  type ThinkingCheckResult,
} from "../services/aiTutorService";
import { getTtsVoice } from "../utils/ttsVoice";
import { authedFetch } from "../utils/authedFetch";
import MathText from "./MathText";
import PlaybackRateControl, { usePlaybackRate } from "./PlaybackRateControl";

const { Text, Paragraph } = Typography;

interface Props {
  /** 题目内容（题干/答案/解析拼接的上下文） */
  exerciseContent: string;
  /** 所属学科 */
  subject?: string;
  /** 题目ID（用于关联） */
  questionId?: number;
  /** 是否可见 */
  visible: boolean;
  /** 跳转到聊天继续追问的回调 */
  onContinueInChat?: () => void;
}

/** 判题结果的展示配置 */
const VERDICT_CONFIG: Record<
  ThinkingCheckResult["verdict"],
  { type: "success" | "warning" | "error"; title: string }
> = {
  correct: { type: "success", title: "✅ 回答正确！" },
  partial: { type: "warning", title: "🟡 基本正确，还可以更完整" },
  wrong: { type: "error", title: "❌ 回答不正确" },
};

/** 判题结果 → 知识状态反馈等级映射 */
const VERDICT_FEEDBACK_LEVEL: Record<
  ThinkingCheckResult["verdict"],
  "完全听懂" | "部分听懂" | "没听懂"
> = {
  correct: "完全听懂",
  partial: "部分听懂",
  wrong: "没听懂",
};

export default function ExplainCard({
  exerciseContent,
  subject = "未知",
  questionId,
  visible,
  onContinueInChat,
}: Props) {
  /** 是否已经开始讲解 */
  const [started, setStarted] = useState(false);
  /** 是否正在加载讲解 */
  const [loading, setLoading] = useState(false);
  /** 讲解结果（完整讲解 + 思考题） */
  const [result, setResult] = useState<ExplainResult | null>(null);
  /** 学生对思考题的回答 */
  const [userAnswer, setUserAnswer] = useState("");
  /** 是否正在判题 */
  const [checking, setChecking] = useState(false);
  /** 判题结果 */
  const [checkResult, setCheckResult] = useState<ThinkingCheckResult | null>(null);

  // ============ TTS 语音播报 ============
  /** 当前播报状态：idle=未播放 loading=合成中 playing=播放中 paused=已暂停 */
  const [playState, setPlayState] = useState<"idle" | "loading" | "playing" | "paused">("idle");
  /** 当前正在播报的文本 key（区分讲解播报与思考题播报） */
  const [playingKey, setPlayingKey] = useState<string>("");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** 音频 Blob URL 缓存：同一段文本重复播放不重复请求合成 */
  const audioCacheRef = useRef<Map<string, string>>(new Map());
  // 播放速度（0.75–1.5）：服务端按原速合成，变速由客户端 Audio.playbackRate 完成；
  // 状态与持久化由共享 hook 管理（key "explain"，与听力/听写独立记忆）
  const { playbackRate, setPlaybackRate, playbackRateRef } = usePlaybackRate("explain");

  /** 调整播放速度（播放中即时生效，并持久化保存，后续播放沿用） */
  const applyPlaybackRate = useCallback((rate: number) => {
    setPlaybackRate(rate);
    if (audioRef.current) audioRef.current.playbackRate = rate;
  }, [setPlaybackRate]);

  /** 停止当前播放（不清缓存） */
  const stopSpeak = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      try { audioRef.current.pause(); } catch { /* ignore */ }
      audioRef.current = null;
    }
    setPlayState("idle");
    setPlayingKey("");
  }, []);

  /** 组件卸载时停止播放并释放缓存的 Blob URL */
  useEffect(() => {
    const cache = audioCacheRef.current;
    return () => {
      abortRef.current?.abort();
      if (audioRef.current) {
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
        try { audioRef.current.pause(); } catch { /* ignore */ }
      }
      cache.forEach((url) => URL.revokeObjectURL(url));
      cache.clear();
    };
  }, []);

  /**
   * 播报一段讲解文本：后端 Edge TTS 合成 MP3 后播放。
   * voice=mixed/mixed_male（中文女声/男声，随助教设置的音色）可同时朗读中英混合文本，适配英语科目讲解。
   * 播放中再次点击同一段 → 暂停；暂停后再点 → 从暂停处继续播放。
   */
  const speakText = useCallback(async (key: string, text: string) => {
    // 播放中再次点击同一段 → 暂停（保留播放进度）
    if (playingKey === key && playState === "playing" && audioRef.current) {
      audioRef.current.pause();
      setPlayState("paused");
      return;
    }
    // 暂停中再次点击同一段 → 从暂停处恢复播放
    if (playingKey === key && playState === "paused" && audioRef.current) {
      try {
        await audioRef.current.play();
        setPlayState("playing");
      } catch {
        message.error("音频播放失败");
        stopSpeak();
      }
      return;
    }
    // 合成中再次点击同一段 → 取消；切换到另一段 → 先停掉当前播放
    if (playingKey === key && playState === "loading") {
      stopSpeak();
      return;
    }
    stopSpeak();

    const cleaned = text.trim().slice(0, 3000); // /oral/tts 单次上限 3000 字符
    if (!cleaned) {
      message.warning("没有可播放的讲解内容");
      return;
    }

    setPlayState("loading");
    setPlayingKey(key);

    try {
      // 按助教设置的音色解析语音（mixed 女声 / mixed_male 男声），缓存 key 含音色避免串音
      const voice = await getTtsVoice("mixed");
      const cacheKey = `${voice}:${cleaned}`;
      let blobUrl = audioCacheRef.current.get(cacheKey);
      if (!blobUrl) {
        const controller = new AbortController();
        abortRef.current = controller;
        // 讲解文本较长，改用 POST 请求体传文本，避免 GET 查询参数超出 URL 长度上限
        // 使用 authedFetch 自动附加 Authorization 头 + 401 自动刷新 token
        const response = await authedFetch("/api/v1/oral/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: cleaned, voice, rate: "+0%" }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`服务端错误 (${response.status})`);
        const blob = await response.blob();
        if (blob.size === 0) throw new Error("服务端返回空音频");
        blobUrl = URL.createObjectURL(blob);
        audioCacheRef.current.set(cacheKey, blobUrl);
      }

      const audio = new Audio(blobUrl);
      audio.playbackRate = playbackRateRef.current;
      audioRef.current = audio;
      audio.onended = () => {
        setPlayState("idle");
        setPlayingKey("");
        audioRef.current = null;
      };
      audio.onerror = () => {
        setPlayState("idle");
        setPlayingKey("");
        audioRef.current = null;
        message.error("音频播放失败");
      };
      await audio.play();
      setPlayState("playing");
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return; // 主动停止，不提示
      console.error("讲解播报失败:", err);
      message.error("语音播报失败，请重试");
      setPlayState("idle");
      setPlayingKey("");
    }
  }, [playingKey, playState, stopSpeak]);

  /** 播报按钮（讲解/思考题通用）：左侧播放速度滑动条，播放中显示暂停，暂停后显示继续播放 */
  const renderSpeakButton = (key: string, text: string) => {
    const active = playingKey === key;
    const icon = active && playState === "loading"
      ? <LoadingOutlined />
      : active && playState === "playing"
        ? <PauseCircleOutlined />
        : active && playState === "paused"
          ? <PlayCircleOutlined />
          : <SoundOutlined />;
    const label = active && playState === "loading"
      ? "合成中"
      : active && playState === "playing"
        ? "暂停"
        : active && playState === "paused"
          ? "继续播放"
          : "播放讲解";
    return (
      <Space size={4}>
        <PlaybackRateControl value={playbackRate} onChange={applyPlaybackRate} width={90} />
        <Button
          size="small"
          type="text"
          icon={icon}
          onClick={() => speakText(key, text)}
          style={{ color: active ? "#1677ff" : undefined }}
        >
          {label}
        </Button>
      </Space>
    );
  };

  /**
   * 开始讲解：调用后端 API 获取完整讲解 + 思考题
   */
  const startExplain = useCallback(async () => {
    if (!exerciseContent) return;

    setLoading(true);
    setStarted(true);
    setResult(null);
    setUserAnswer("");
    setCheckResult(null);

    try {
      const data = await explainExercise({
        exercise_content: exerciseContent,
        subject,
        explanation_style: "直接讲解式",
        strict_level: 3,
        // 关联题目 ID：后端读取切割原图做多模态讲解，让 AI 真正看到题干
        question_id: questionId,
      });
      setResult(data);
    } catch (err) {
      console.error("加载讲解失败:", err);
      message.error((err as Error)?.message || "讲解加载失败，请重试");
      setStarted(false);
    } finally {
      setLoading(false);
    }
  }, [exerciseContent, subject, questionId]);

  /**
   * 提交思考题回答：AI 判定对错，并将结果记录到知识状态系统
   */
  const submitAnswer = useCallback(async () => {
    if (!result || !userAnswer.trim()) return;
    stopSpeak();
    setChecking(true);

    try {
      const check = await checkThinkingAnswer({
        exercise_content: exerciseContent,
        thinking_question: result.thinking_question,
        user_answer: userAnswer.trim(),
        subject,
      });
      setCheckResult(check);

      // 判题结果同步到知识状态系统（失败不阻断流程）
      const kp = result.knowledge_points[0] || "";
      if (kp) {
        recordFeedback({
          knowledge_point: kp,
          feedback_level: VERDICT_FEEDBACK_LEVEL[check.verdict],
          question_id: questionId ? String(questionId) : undefined,
        }).catch(() => { /* 反馈记录失败不阻断流程 */ });
      }
    } catch (err) {
      console.error("判题失败:", err);
      message.error((err as Error)?.message || "判题失败，请重试");
    } finally {
      setChecking(false);
    }
  }, [result, userAnswer, exerciseContent, subject, questionId, stopSpeak]);

  if (!visible) return null;

  return (
    <div
      style={{
        marginTop: 16,
        padding: "12px 16px",
        background: "#fafafa",
        borderRadius: 8,
        border: "1px solid #e8e8e8",
      }}
    >
      {/* 未开始时显示开始按钮 */}
      {!started && !loading && (
        <div style={{ textAlign: "center" }}>
          <Button
            type="primary"
            icon={<MessageOutlined />}
            onClick={startExplain}
          >
            助教讲解
          </Button>
        </div>
      )}

      {/* 加载中 */}
      {loading && (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <Spin tip="AI 正在准备讲解..." />
        </div>
      )}

      {/* 讲解内容 */}
      {started && !loading && result && (
        <div>
          {/* 知识点标签 */}
          {result.knowledge_points.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                本题知识点：
              </Text>
              {result.knowledge_points.map((kp, i) => (
                <Tag key={i} color="blue" style={{ marginTop: 2 }}>
                  {kp}
                </Tag>
              ))}
            </div>
          )}

          {/* 完整讲解（含播报按钮） */}
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <Text strong style={{ fontSize: 13, color: "#1677ff" }}>
              📖 题目讲解
            </Text>
            {renderSpeakButton("explanation", result.explanation)}
          </Space>
          <MathText
            content={result.explanation}
            style={{
              display: "block",
              marginTop: 8,
              padding: "8px 12px",
              background: "#fff",
              borderRadius: 6,
              border: "1px solid #f0f0f0",
              fontSize: 14,
            }}
          />

          {/* 思考题 */}
          <div
            style={{
              padding: "8px 12px",
              background: "#fff7e6",
              borderRadius: 6,
              border: "1px solid #ffd591",
            }}
          >
            <Space style={{ width: "100%", justifyContent: "space-between" }}>
              <Text strong style={{ fontSize: 13, color: "#fa8c16" }}>
                🤔 思考题
              </Text>
              {renderSpeakButton("thinking-question", result.thinking_question)}
            </Space>
            <MathText
              content={result.thinking_question}
              style={{ display: "block", marginTop: 4, marginBottom: 0, fontSize: 14 }}
            />
          </div>

          {/* 作答区（未出结果时显示） */}
          {!checkResult && (
            <div style={{ marginTop: 12 }}>
              <Input.TextArea
                rows={3}
                value={userAnswer}
                onChange={(e) => setUserAnswer(e.target.value)}
                placeholder="在这里写下你的回答..."
                maxLength={1000}
                disabled={checking}
              />
              <Button
                type="primary"
                size="small"
                icon={<SendOutlined />}
                style={{ marginTop: 8 }}
                loading={checking}
                disabled={!userAnswer.trim()}
                onClick={submitAnswer}
              >
                提交回答
              </Button>
            </div>
          )}

          {/* 判题结果 */}
          {checkResult && (
            <div style={{ marginTop: 12 }}>
              <Alert
                type={VERDICT_CONFIG[checkResult.verdict].type}
                message={VERDICT_CONFIG[checkResult.verdict].title}
                description={
                  <>
                    <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                      我的回答：{userAnswer}
                    </Text>
                    {checkResult.feedback}
                  </>
                }
                showIcon={false}
              />
              <Space style={{ marginTop: 8 }}>
                {checkResult.verdict !== "correct" && (
                  <Button
                    size="small"
                    icon={<RedoOutlined />}
                    onClick={() => setCheckResult(null)}
                  >
                    重新作答
                  </Button>
                )}
                {onContinueInChat && (
                  <Button
                    type="link"
                    size="small"
                    icon={<MessageOutlined />}
                    onClick={onContinueInChat}
                  >
                    在聊天中继续追问
                  </Button>
                )}
              </Space>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
