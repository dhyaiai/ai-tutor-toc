/**
 * 答案切割弹窗 —— 上传标准答案（答案解析）文件，框选每道题的标准答案区域。
 *
 * 切出的标准答案会在 AI 评分时拼接在题目图下方（标注为 Reference Answer），
 * 作为 correct_answer 的权威依据，不会被当作学生作答。
 *
 * 与 ManualSplitModal 类似，但：
 * 1. 初始步骤需要先上传答案文件
 * 2. 区域匹配到已有题号（通过下拉选择），而非自动递增
 * 3. 提交时调用 answer-split 端点
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Modal, Button, InputNumber, message, Space, Spin, Empty, Popconfirm, Tooltip, Upload, Select } from 'antd';
import { DeleteOutlined, LeftOutlined, RightOutlined, PlusOutlined, UploadOutlined, InboxOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import {
  assignmentService,
  PageInfo,
} from '../services/assignmentService';
import type { QuestionItem } from '../services/assignmentService';
import { rotateImageDataUrl } from '../utils/imageUtils';

const { Dragger } = Upload;

// ── Types ──

interface DrawnRegion {
  id: string;
  question_number: number;
  page_index: number;
  x: number;
  y: number;
  w: number;
  h: number;
  draw_order: number;
}

export interface AnswerRegion {
  question_number: number;
  page_index: number;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation?: number;
}

interface AnswerSplitModalProps {
  assignmentId: number;
  /** 已有题目列表（用于题号选择） */
  questions: Array<{ id: number; question_number: number }>;
  visible: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}

// ── Constants ──

const COLORS = [
  '#FF4D4F', '#1890FF', '#52C41A', '#FAAD14', '#722ED1',
  '#EB2F96', '#13C2C2', '#F5222D', '#2F54EB', '#FA541C',
];

const HANDLE_SIZE = 8;
type HandleDir = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move';

// ── Helpers ──

let _idCounter = 0;
function uid(): string {
  return `ar${++_idCounter}_${Date.now()}`;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ── Component ──

const AnswerSplitModal: React.FC<AnswerSplitModalProps> = ({
  assignmentId,
  questions,
  visible,
  onSuccess,
  onCancel,
}) => {
  // 答案上传步骤
  const [uploaded, setUploaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [answerFileUrl, setAnswerFileUrl] = useState('');

  // Canvas 状态（与 ManualSplitModal 共用模式）
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [pages, setPages] = useState<PageInfo[]>([]);
  const [currentPage, setCurrentPage] = useState(0);
  const [regions, setRegions] = useState<DrawnRegion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawing, setDrawing] = useState<{
    startX: number;
    startY: number;
    endX: number;
    endY: number;
  } | null>(null);
  const [resizing, setResizing] = useState<{
    regionId: string;
    handle: HandleDir;
    origRegion: { x: number; y: number; w: number; h: number };
    startX: number;
    startY: number;
  } | null>(null);
  const [pendingQuestionNum, setPendingQuestionNum] = useState<number | null>(null);
  // 旋转
  const [pageRotations, setPageRotations] = useState<Record<number, number>>({});
  const [displayUrl, setDisplayUrl] = useState<string>('');
  const [displaySize, setDisplaySize] = useState<{ width: number; height: number } | null>(null);

  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const drawOrderRef = useRef(0);

  const page = pages[currentPage];
  const currentRotation = pageRotations[currentPage] || 0;

  // ── 上传答案文件 ──
  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const data = await assignmentService.uploadAnswerFile(assignmentId, file);
      setPages(data.pages);
      setAnswerFileUrl((data as any).answer_file_url || '');
      setUploaded(true);
      setRegions([]);
      setSelectedId(null);
      setCurrentPage(0);
      setPageRotations({});
      drawOrderRef.current = 0;
      message.success(`答案文件已上传，共 ${data.total_pages} 页`);
    } catch (e: any) {
      message.error('上传失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'));
    } finally {
      setUploading(false);
    }
    return false; // 阻止默认上传行为
  };

  // ── 重置状态 ──
  useEffect(() => {
    if (visible) {
      setUploaded(false);
      setAnswerFileUrl('');
      setPages([]);
      setRegions([]);
      setSelectedId(null);
      setCurrentPage(0);
      setPageRotations({});
      drawOrderRef.current = 0;
    }
  }, [visible]);

  // ── 旋转图片生成（共用实现见 utils/imageUtils.ts） ──

  useEffect(() => {
    if (!page) {
      setDisplayUrl('');
      setDisplaySize(null);
      return;
    }
    if (currentRotation === 0) {
      setDisplayUrl(page.image_url);
      setDisplaySize({ width: page.width, height: page.height });
    } else {
      rotateImageDataUrl(page.image_url, currentRotation)
        .then((dataUrl) => {
          setDisplayUrl(dataUrl);
          if (currentRotation === 90 || currentRotation === 270) {
            setDisplaySize({ width: page.height, height: page.width });
          } else {
            setDisplaySize({ width: page.width, height: page.height });
          }
        })
        .catch(() => {
          setDisplayUrl(page.image_url);
          setDisplaySize({ width: page.width, height: page.height });
        });
    }
  }, [page, currentRotation, rotateImageDataUrl]);

  // ── Redraw canvas ──
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !page) return;

    const refW = displaySize?.width ?? page.width;
    const refH = displaySize?.height ?? page.height;
    const scaleX = img.clientWidth / refW;
    const scaleY = img.clientHeight / refH;

    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
    const ctx = canvas.getContext('2d')!;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const pageRegions = regions.filter((r) => r.page_index === currentPage);
    pageRegions.forEach((r, idx) => {
      const rx = r.x * scaleX;
      const ry = r.y * scaleY;
      const rw = r.w * scaleX;
      const rh = r.h * scaleY;
      const isSelected = r.id === selectedId;
      const color = COLORS[idx % COLORS.length];

      ctx.fillStyle = isSelected ? color + '30' : color + '15';
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeStyle = isSelected ? color : color + '99';
      ctx.lineWidth = isSelected ? 2.5 : 1.5;
      ctx.strokeRect(rx, ry, rw, rh);

      const label = `第${r.question_number}题答案`;
      const fontSize = Math.max(12, 14 * scaleX);
      ctx.fillStyle = color;
      ctx.font = `bold ${fontSize}px sans-serif`;
      const textY = Math.max(ry + fontSize + 2, fontSize + 2);
      ctx.fillText(label, rx + 4 * scaleX, textY);

      if (isSelected) {
        const hs = HANDLE_SIZE * scaleX;
        const handlePositions: Array<[number, number, HandleDir]> = [
          [rx, ry, 'nw'], [rx + rw / 2, ry, 'n'], [rx + rw, ry, 'ne'],
          [rx + rw, ry + rh / 2, 'e'], [rx + rw, ry + rh, 'se'],
          [rx + rw / 2, ry + rh, 's'], [rx, ry + rh, 'sw'],
          [rx, ry + rh / 2, 'w'],
        ];
        handlePositions.forEach(([hx, hy]) => {
          ctx.fillStyle = '#fff';
          ctx.fillRect(hx - hs / 2, hy - hs / 2, hs, hs);
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.strokeRect(hx - hs / 2, hy - hs / 2, hs, hs);
        });
      }
    });

    // 拖拽预览（drawing 存的是原图坐标，绘制时需换算回画布坐标）
    if (drawing) {
      const sx = Math.min(drawing.startX, drawing.endX) * scaleX;
      const sy = Math.min(drawing.startY, drawing.endY) * scaleY;
      const sw = Math.abs(drawing.endX - drawing.startX) * scaleX;
      const sh = Math.abs(drawing.endY - drawing.startY) * scaleY;
      ctx.strokeStyle = '#52C41A';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 3]);
      ctx.strokeRect(sx, sy, sw, sh);
      ctx.setLineDash([]);
      ctx.fillStyle = '#52C41A15';
      ctx.fillRect(sx, sy, sw, sh);
    }
  }, [regions, selectedId, drawing, currentPage, page, displaySize]);

  useEffect(() => { redraw(); }, [redraw]);
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const onLoad = () => redraw();
    img.addEventListener('load', onLoad);
    return () => img.removeEventListener('load', onLoad);
  }, [redraw, page]);

  // ── Coordinate helpers ──
  const canvasToOriginal = useCallback(
    (canvasX: number, canvasY: number): { x: number; y: number } | null => {
      const img = imgRef.current;
      if (!img || !page) return null;
      const refW = displaySize?.width ?? page.width;
      const refH = displaySize?.height ?? page.height;
      const scaleX = refW / img.clientWidth;
      const scaleY = refH / img.clientHeight;
      return { x: canvasX * scaleX, y: canvasY * scaleY };
    },
    [page, displaySize],
  );

  const getHandleAt = useCallback(
    (mx: number, my: number): { regionId: string; handle: HandleDir } | null => {
      if (!selectedId || !page) return null;
      const sel = regions.find((r) => r.id === selectedId);
      if (!sel || sel.page_index !== currentPage) return null;
      const img = imgRef.current!;
      const refW = displaySize?.width ?? page.width;
      const refH = displaySize?.height ?? page.height;
      const scaleX = img.clientWidth / refW;
      const scaleY = img.clientHeight / refH;
      const rx = sel.x * scaleX;
      const ry = sel.y * scaleY;
      const rw = sel.w * scaleX;
      const rh = sel.h * scaleY;
      const hs = HANDLE_SIZE;
      const handlePositions: Array<[number, number, HandleDir]> = [
        [rx, ry, 'nw'], [rx + rw / 2, ry, 'n'], [rx + rw, ry, 'ne'],
        [rx + rw, ry + rh / 2, 'e'], [rx + rw, ry + rh, 'se'],
        [rx + rw / 2, ry + rh, 's'], [rx, ry + rh, 'sw'],
        [rx, ry + rh / 2, 'w'],
      ];
      for (const [hx, hy, dir] of handlePositions) {
        if (Math.abs(mx - hx) <= hs && Math.abs(my - hy) <= hs) {
          return { regionId: sel.id, handle: dir };
        }
      }
      return null;
    },
    [selectedId, regions, currentPage, page, displaySize],
  );

  // ── Mouse handlers ──
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const orig = canvasToOriginal(mx, my);
    if (!orig) return;

    const handleHit = getHandleAt(mx, my);
    if (handleHit) {
      const sel = regions.find((r) => r.id === handleHit.regionId)!;
      setResizing({
        regionId: handleHit.regionId,
        handle: handleHit.handle,
        origRegion: { x: sel.x, y: sel.y, w: sel.w, h: sel.h },
        startX: orig.x,
        startY: orig.y,
      });
      return;
    }

    if (selectedId) {
      const sel = regions.find((r) => r.id === selectedId);
      if (sel && sel.page_index === currentPage) {
        const img = imgRef.current!;
        const refW = displaySize?.width ?? page!.width;
        const refH = displaySize?.height ?? page!.height;
        const scaleX = img.clientWidth / refW;
        const scaleY = img.clientHeight / refH;
        const rx = sel.x * scaleX;
        const ry = sel.y * scaleY;
        const rw = sel.w * scaleX;
        const rh = sel.h * scaleY;
        if (mx >= rx && mx <= rx + rw && my >= ry && my <= ry + rh) {
          setResizing({
            regionId: sel.id,
            handle: 'move',
            origRegion: { x: sel.x, y: sel.y, w: sel.w, h: sel.h },
            startX: orig.x,
            startY: orig.y,
          });
          return;
        }
      }
    }

    const clicked = [...regions]
      .filter((r) => r.page_index === currentPage)
      .reverse()
      .find((r) => orig.x >= r.x && orig.x <= r.x + r.w && orig.y >= r.y && orig.y <= r.y + r.h);
    if (clicked) {
      setSelectedId(clicked.id);
      return;
    }

    setSelectedId(null);
    setDrawing({ startX: orig.x, startY: orig.y, endX: orig.x, endY: orig.y });
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const orig = canvasToOriginal(mx, my);
    if (!orig) return;

    if (resizing) {
      const { regionId, handle, origRegion, startX, startY } = resizing;
      const dx = orig.x - startX;
      const dy = orig.y - startY;
      const pw = displaySize?.width ?? page!.width;
      const ph = displaySize?.height ?? page!.height;
      setRegions((prev) =>
        prev.map((r) => {
          if (r.id !== regionId) return r;
          let { x, y, w, h } = origRegion;
          if (handle === 'move') {
            x = clamp(x + dx, 0, pw - w);
            y = clamp(y + dy, 0, ph - h);
          } else {
            if (handle.includes('e')) w = clamp(w + dx, 20, pw - x);
            if (handle.includes('w')) { const newX = clamp(x + dx, 0, x + w - 20); w = w + (x - newX); x = newX; }
            if (handle.includes('s')) h = clamp(h + dy, 20, ph - y);
            if (handle.includes('n')) { const newY = clamp(y + dy, 0, y + h - 20); h = h + (y - newY); y = newY; }
          }
          return { ...r, x, y, w, h };
        }),
      );
      return;
    }

    if (drawing) {
      setDrawing((prev) => (prev ? { ...prev, endX: orig.x, endY: orig.y } : null));
      return;
    }

    const handleHit = getHandleAt(mx, my);
    const cursor = handleHit ? getResizeCursor(handleHit.handle) : 'crosshair';
    canvas.style.cursor = cursor;
  };

  const handleMouseUp = () => {
    if (resizing) { setResizing(null); return; }
    if (!drawing) return;

    const x = Math.min(drawing.startX, drawing.endX);
    const y = Math.min(drawing.startY, drawing.endY);
    const w = Math.abs(drawing.endX - drawing.startX);
    const h = Math.abs(drawing.endY - drawing.startY);

    if (w >= 20 && h >= 20) {
      let questionNum: number;
      if (pendingQuestionNum !== null) {
        questionNum = pendingQuestionNum;
        setPendingQuestionNum(null);
      } else {
        // 答案切割：默认自动递增——取尚未分配区域的最小题号；全部分配完则回退到最后一题
        const sortedQs = [...questions].sort((a, b) => a.question_number - b.question_number);
        const usedNums = new Set(regions.map((r) => r.question_number));
        const nextQ = sortedQs.find((q) => !usedNums.has(q.question_number));
        questionNum = nextQ
          ? nextQ.question_number
          : sortedQs.length > 0
            ? sortedQs[sortedQs.length - 1].question_number
            : 1;
      }
      drawOrderRef.current += 1;
      const newRegion: DrawnRegion = {
        id: uid(),
        question_number: questionNum,
        page_index: currentPage,
        x: clamp(x, 0, (displaySize?.width ?? page!.width) - 1),
        y: clamp(y, 0, (displaySize?.height ?? page!.height) - 1),
        w,
        h,
        draw_order: drawOrderRef.current,
      };
      setRegions((prev) => [...prev, newRegion]);
      setSelectedId(newRegion.id);
    }
    setDrawing(null);
  };

  // ── Keyboard shortcuts ──
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // 在输入框/文本域内按 Backspace/Delete 是编辑操作，不应触发删除切割区域
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (e.key === 'Escape') {
        if (pendingQuestionNum !== null) {
          setPendingQuestionNum(null);
          message.info('已取消额外区域添加');
          return;
        }
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedId) {
          setRegions((prev) => prev.filter((r) => r.id !== selectedId));
          setSelectedId(null);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedId, pendingQuestionNum]);

  // ── Region management ──
  const deleteRegion = (id: string) => {
    setRegions((prev) => prev.filter((r) => r.id !== id));
    if (selectedId === id) setSelectedId(null);
  };

  const updateQuestionNumber = (id: string, num: number) => {
    setRegions((prev) =>
      prev.map((r) => (r.id === id ? { ...r, question_number: num } : r)),
    );
  };

  const startExtraRegion = (sourceRegion: DrawnRegion) => {
    setPendingQuestionNum(sourceRegion.question_number);
    setCurrentPage(sourceRegion.page_index);
    setSelectedId(null);
    message.info(`请在第${sourceRegion.page_index + 1}页上框选第${sourceRegion.question_number}题的额外答案区域，按 Esc 取消`);
  };

  const sortedPageRegions = regions
    .filter((r) => r.page_index === currentPage)
    .sort((a, b) => a.y - b.y || a.x - b.x);

  const allRegionsSorted = [...regions].sort(
    (a, b) => a.question_number - b.question_number || a.draw_order - b.draw_order,
  );

  // ── Submit ──
  const doSubmit = async () => {
    const sorted = [...regions].sort(
      (a, b) => a.question_number - b.question_number || a.draw_order - b.draw_order,
    );

    const payload: AnswerRegion[] = sorted.map((r) => ({
      question_number: r.question_number,
      page_index: r.page_index,
      x: r.x,
      y: r.y,
      w: r.w,
      h: r.h,
      rotation: pageRotations[r.page_index] || 0,
    }));

    setSubmitting(true);
    try {
      await assignmentService.saveAnswerSplit(assignmentId, payload, answerFileUrl);
      message.success(`答案切割完成，共 ${payload.length} 个区域`);
      onSuccess();
    } catch (e: any) {
      message.error('切割失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = () => {
    if (regions.length === 0) {
      message.warning('请至少绘制一个答案区域');
      return;
    }
    if (!answerFileUrl) {
      message.error('缺少答案文件路径，请重新上传');
      return;
    }

    // 校验切割数量：已框选的题号数少于题目列表题数时提醒用户
    const coveredNums = new Set(regions.map((r) => r.question_number));
    const missingNums = questions
      .map((q) => q.question_number)
      .filter((n) => !coveredNums.has(n))
      .sort((a, b) => a - b);
    if (missingNums.length > 0) {
      Modal.confirm({
        title: '切割数量与题目数量不一致',
        content: (
          <div>
            <p>题目列表共 {questions.length} 题，当前仅切割了 {coveredNums.size} 题。</p>
            <p>未切割的题号：{missingNums.map((n) => `第${n}题`).join('、')}</p>
            <p>是否仍要提交？未切割的题目将没有标准答案。</p>
          </div>
        ),
        okText: '仍然提交',
        cancelText: '返回补充',
        onOk: doSubmit,
      });
      return;
    }

    doSubmit();
  };

  // ── 可选题号列表 ──
  const questionNumberOptions = questions.map((q) => ({
    value: q.question_number,
    label: `第${q.question_number}题`,
  }));

  // ── Render ──
  return (
    <Modal
      title="答案切割"
      open={visible}
      onCancel={onCancel}
      width="95vw"
      style={{ top: 20 }}
      footer={
        uploaded ? (
          <Space>
            <Button onClick={onCancel}>取消</Button>
            <Button type="primary" loading={submitting} onClick={handleSubmit}>
              确认切割
            </Button>
          </Space>
        ) : null
      }
      destroyOnClose
    >
      {!uploaded ? (
        /* ── 步骤1：上传答案文件 ── */
        <div style={{ padding: 20 }}>
          <Dragger
            accept=".pdf,.png,.jpg,.jpeg,.webp"
            maxCount={1}
            beforeUpload={handleUpload}
            showUploadList={false}
            disabled={uploading}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">
              {uploading ? '正在上传并处理...' : '点击或拖拽上传标准答案（答案解析）文件'}
            </p>
            <p className="ant-upload-hint">
              支持 PDF、PNG、JPG、WebP 格式，最大 50MB
            </p>
          </Dragger>
          {uploading && <Spin style={{ display: 'block', marginTop: 16 }} tip="渲染答案页面中..." />}
        </div>
      ) : !page ? (
        <Empty description="无法加载答案文件" />
      ) : (
        /* ── 步骤2：Canvas 框选答案区域 ── */
        <div style={{ display: 'flex', gap: 16, height: '70vh' }}>
          {/* Canvas area */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              {pendingQuestionNum !== null ? (
                <span style={{
                  color: '#fff', background: '#52C41A', padding: '4px 12px',
                  borderRadius: 4, fontWeight: 'bold', fontSize: 13,
                }}>
                  🔲 请框选第{pendingQuestionNum}题的额外答案区域（按 Esc 取消）
                </span>
              ) : (
                <span style={{ color: '#888' }}>
                  拖拽绘制矩形框选答案区域，通过右侧面板选择题号
                </span>
              )}
              {/* 旋转按钮 */}
              <div style={{ borderLeft: '1px solid #d9d9d9', paddingLeft: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: '#888', marginRight: 4 }}>旋转：</span>
                <Button size="small" type={currentRotation === 90 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 270 ? 0 : (currentRotation + 90) as 0 | 90 | 180 | 270;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}>↻ 90°</Button>
                <Button size="small" type={currentRotation === 180 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 180 ? 0 : 180;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}>180°</Button>
                <Button size="small" type={currentRotation === 270 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 0 ? 270 : ((currentRotation - 90 + 360) % 360) as 0 | 90 | 180 | 270;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}>↺ 90°</Button>
                {currentRotation !== 0 && (
                  <Button size="small" onClick={() => setPageRotations((prev) => ({ ...prev, [currentPage]: 0 }))}>重置</Button>
                )}
              </div>
            </div>

            {pages.length > 1 && (
              <div style={{ marginBottom: 8, textAlign: 'center' }}>
                <Space>
                  <Button size="small" icon={<LeftOutlined />} disabled={currentPage === 0}
                    onClick={() => { setCurrentPage((p) => p - 1); setSelectedId(null); }} />
                  <span>第 {currentPage + 1} / {pages.length} 页</span>
                  <Button size="small" icon={<RightOutlined />} disabled={currentPage === pages.length - 1}
                    onClick={() => { setCurrentPage((p) => p + 1); setSelectedId(null); }} />
                </Space>
              </div>
            )}

            <div ref={containerRef} style={{
              flex: 1, overflow: 'auto', border: '1px solid #d9d9d9',
              borderRadius: 4, background: '#f5f5f5', position: 'relative',
            }}>
              <div style={{ position: 'relative', display: 'inline-block' }}>
                <img ref={imgRef} src={displayUrl || page.image_url}
                  alt={`Answer Page ${currentPage + 1}`}
                  style={{ display: 'block', maxWidth: '100%' }}
                  draggable={false} onLoad={() => redraw()} />
                <canvas ref={canvasRef}
                  style={{ position: 'absolute', top: 0, left: 0, cursor: 'crosshair' }}
                  onMouseDown={handleMouseDown} onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp} onMouseLeave={handleMouseUp} />
              </div>
            </div>
          </div>

          {/* Side panel */}
          <div style={{ width: 300, flexShrink: 0, border: '1px solid #d9d9d9', borderRadius: 4, padding: 12, overflow: 'auto', background: '#fafafa' }}>
            <h4 style={{ margin: '0 0 8px' }}>已选答案区域 ({regions.length})</h4>
            {allRegionsSorted.length === 0 ? (
              <div style={{ color: '#999', fontSize: 13 }}>在图片上拖拽绘制答案区域</div>
            ) : (
              allRegionsSorted.map((r) => {
                const isSelected = r.id === selectedId;
                const isOnCurrentPage = r.page_index === currentPage;
                const color = COLORS[allRegionsSorted.indexOf(r) % COLORS.length];
                return (
                  <div key={r.id}
                    onClick={() => { setSelectedId(r.id); if (!isOnCurrentPage) setCurrentPage(r.page_index); }}
                    style={{
                      padding: 8, marginBottom: 6, borderRadius: 4,
                      border: `2px solid ${isSelected ? color : '#e8e8e8'}`,
                      background: isSelected ? color + '15' : '#fff',
                      cursor: 'pointer', opacity: isOnCurrentPage ? 1 : 0.5,
                    }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <Space size={4}>
                        <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: color }} />
                        <strong>第</strong>
                        <Select
                          size="small"
                          value={r.question_number}
                          onChange={(v) => updateQuestionNumber(r.id, v)}
                          options={questionNumberOptions}
                          style={{ width: 90 }}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <strong>题</strong>
                      </Space>
                      <Space size={2}>
                        <Tooltip title="添加同题额外区域">
                          <Button size="small" type="text" icon={<PlusOutlined />}
                            onClick={(e) => { e.stopPropagation(); startExtraRegion(r); }} />
                        </Tooltip>
                        <Popconfirm title="确认删除此区域？"
                          onConfirm={(e) => { e?.stopPropagation(); deleteRegion(r.id); }}
                          onCancel={(e) => e?.stopPropagation()}>
                          <Button size="small" danger type="text" icon={<DeleteOutlined />}
                            onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>
                      </Space>
                    </div>
                    <div style={{ fontSize: 11, color: '#888' }}>
                      答案页{r.page_index + 1} · {Math.round(r.w)}×{Math.round(r.h)}px
                    </div>
                  </div>
                );
              })
            )}
            <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
              提示：为每道已有题目框选对应的答案区域。拖动边角可调整大小，点击内部可移动。点击"+"添加同题额外区域。
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
};

function getResizeCursor(handle: HandleDir): string {
  const cursors: Record<HandleDir, string> = {
    nw: 'nwse-resize', n: 'ns-resize', ne: 'nesw-resize',
    e: 'ew-resize', se: 'nwse-resize', s: 'ns-resize',
    sw: 'nesw-resize', w: 'ew-resize', move: 'move',
  };
  return cursors[handle];
}

export default AnswerSplitModal;
