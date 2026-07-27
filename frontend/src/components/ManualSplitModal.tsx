import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Modal, Button, InputNumber, message, Space, Spin, Empty, Popconfirm, Tooltip } from 'antd';
import { DeleteOutlined, LeftOutlined, RightOutlined, PlusOutlined } from '@ant-design/icons';
import {
  assignmentService,
  PageInfo,
  ManualRegion,
} from '../services/assignmentService';

// ── Types ──

interface DrawnRegion {
  id: string;
  question_number: number;
  page_index: number;
  x: number; // original image pixels
  y: number;
  w: number;
  h: number;
  draw_order: number; // 绘制顺序，用于保持同题多区域的先后关系
}

interface ManualSplitModalProps {
  assignmentId: number;
  visible: boolean;
  /** Existing question bboxes for pre-filling (single question adjust mode) */
  prefillRegion?: {
    question_id: number;
    page_index: number;
    x: number;
    y: number;
    w: number;
    h: number;
  } | null;
  /** Called after successful split/adjust */
  onSuccess: () => void;
  onCancel: () => void;
}

// ── Constants ──

const COLORS = [
  '#FF4D4F', '#1890FF', '#52C41A', '#FAAD14', '#722ED1',
  '#EB2F96', '#13C2C2', '#F5222D', '#2F54EB', '#FA541C',
];

// Resize handle directions (including 'move' for drag-to-move)
const HANDLE_SIZE = 8;
type HandleDir = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move';

// ── Helpers ──

let _idCounter = 0;
function uid(): string {
  return `r${++_idCounter}_${Date.now()}`;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ── Component ──

const ManualSplitModal: React.FC<ManualSplitModalProps> = ({
  assignmentId,
  visible,
  prefillRegion,
  onSuccess,
  onCancel,
}) => {
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [pages, setPages] = useState<PageInfo[]>([]);
  const [currentPage, setCurrentPage] = useState(0);
  const [totalQuestions, setTotalQuestions] = useState<number>(0);
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
  // 每页独立旋转角度：{ [page_index]: 0 | 90 | 180 | 270 }
  const [pageRotations, setPageRotations] = useState<Record<number, number>>({});
  // 旋转后的显示图片 URL（data URL）
  const [displayUrl, setDisplayUrl] = useState<string>('');
  const [displaySize, setDisplaySize] = useState<{ width: number; height: number } | null>(null);

  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const drawOrderRef = useRef(0);

  const page = pages[currentPage];
  const currentRotation = pageRotations[currentPage] || 0;

  // ── 旋转图片生成函数 ──
  const rotateImageDataUrl = useCallback(
    (imageUrl: string, rotation: number): Promise<string> => {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          const canvas = document.createElement('canvas');
          const ctx = canvas.getContext('2d')!;

          if (rotation === 90 || rotation === 270) {
            canvas.width = img.naturalHeight;
            canvas.height = img.naturalWidth;
          } else {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
          }

          ctx.translate(canvas.width / 2, canvas.height / 2);
          ctx.rotate((rotation * Math.PI) / 180);
          ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);

          resolve(canvas.toDataURL('image/png'));
        };
        img.onerror = () => reject(new Error('图片加载失败'));
        img.src = imageUrl;
      });
    },
    [],
  );

  // ── 旋转角度改变时重新生成显示图片 ──
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

  // ── Load source pages ──
  useEffect(() => {
    if (!visible) return;
    (async () => {
      setLoading(true);
      try {
        const data = await assignmentService.getSourcePages(assignmentId);
        setPages(data.pages);

        // Pre-fill for adjust mode
        if (prefillRegion && data.pages[prefillRegion.page_index]) {
          setCurrentPage(prefillRegion.page_index);
          drawOrderRef.current += 1;
          const initRegion: DrawnRegion = {
            id: uid(),
            question_number: prefillRegion.question_id,
            page_index: prefillRegion.page_index,
            x: prefillRegion.x,
            y: prefillRegion.y,
            w: prefillRegion.w,
            h: prefillRegion.h,
            draw_order: drawOrderRef.current,
          };
          setRegions([initRegion]);
          setSelectedId(initRegion.id);
        } else {
          setCurrentPage(0);
          setRegions([]);
          setSelectedId(null);
          setTotalQuestions(0);
          drawOrderRef.current = 0;
        }
      } catch (e: any) {
        message.error('加载源文件失败: ' + (e?.message || '请稍后重试'));
      } finally {
        setLoading(false);
      }
    })();
  }, [visible, assignmentId]); // eslint-disable-line

  // ── Redraw canvas ──
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !page) return;

    // 使用旋转后的显示尺寸进行坐标换算
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

      // Fill
      ctx.fillStyle = isSelected ? color + '30' : color + '15';
      ctx.fillRect(rx, ry, rw, rh);

      // Border
      ctx.strokeStyle = isSelected ? color : color + '99';
      ctx.lineWidth = isSelected ? 2.5 : 1.5;
      ctx.strokeRect(rx, ry, rw, rh);

      // Question number label
      const label = `第${r.question_number}题`;
      const fontSize = Math.max(12, 14 * scaleX);
      ctx.fillStyle = color;
      ctx.font = `bold ${fontSize}px sans-serif`;
      const textY = Math.max(ry + fontSize + 2, fontSize + 2);
      ctx.fillText(label, rx + 4 * scaleX, textY);

      // Resize handles (always visible for selected, on hover for others)
      if (isSelected) {
        const hs = HANDLE_SIZE * scaleX;
        // 8 handles: corners + edges
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

    // Drawing preview
    if (drawing) {
      const sx = Math.min(drawing.startX, drawing.endX);
      const sy = Math.min(drawing.startY, drawing.endY);
      const sw = Math.abs(drawing.endX - drawing.startX);
      const sh = Math.abs(drawing.endY - drawing.startY);
      ctx.strokeStyle = '#1890FF';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 3]);
      ctx.strokeRect(sx, sy, sw, sh);
      ctx.setLineDash([]);
    }
  }, [regions, selectedId, drawing, currentPage, page]);

  useEffect(() => {
    redraw();
  }, [redraw]);

  // Redraw on image load
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
    [selectedId, regions, currentPage, page],
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

    // Check resize handle first
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

    // Check if clicking inside selected region to move it
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
          // Track move offset
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

    // Check if clicking on an existing region to select
    const clicked = [...regions]
      .filter((r) => r.page_index === currentPage)
      .reverse()
      .find((r) => orig.x >= r.x && orig.x <= r.x + r.w && orig.y >= r.y && orig.y <= r.y + r.h);
    if (clicked) {
      setSelectedId(clicked.id);
      return;
    }

    // Start new rectangle
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
            // Resize based on which handle
            if (handle.includes('e')) {
              w = clamp(w + dx, 20, pw - x);
            }
            if (handle.includes('w')) {
              const newX = clamp(x + dx, 0, x + w - 20);
              w = w + (x - newX);
              x = newX;
            }
            if (handle.includes('s')) {
              h = clamp(h + dy, 20, ph - y);
            }
            if (handle.includes('n')) {
              const newY = clamp(y + dy, 0, y + h - 20);
              h = h + (y - newY);
              y = newY;
            }
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

    // Update cursor for resize handles
    const handleHit = getHandleAt(mx, my);
    const cursor = handleHit ? getResizeCursor(handleHit.handle) : 'crosshair';
    canvas.style.cursor = cursor;
  };

  const handleMouseUp = () => {
    if (resizing) {
      setResizing(null);
      return;
    }

    if (!drawing) return;

    const x = Math.min(drawing.startX, drawing.endX);
    const y = Math.min(drawing.startY, drawing.endY);
    const w = Math.abs(drawing.endX - drawing.startX);
    const h = Math.abs(drawing.endY - drawing.startY);

    // Minimum size: 20px in each direction
    if (w >= 20 && h >= 20) {
      // If pendingQuestionNum is set, use it (user clicked + to add extra region);
      // otherwise auto-increment (adjust mode always belongs to the same question)
      let questionNum: number;
      if (pendingQuestionNum !== null) {
        questionNum = pendingQuestionNum;
        setPendingQuestionNum(null);
      } else if (prefillRegion) {
        questionNum = prefillRegion.question_id;
      } else {
        questionNum =
          regions.length > 0
            ? Math.max(...regions.map((r) => r.question_number)) + 1
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

  /** 点击 + 后进入"待框选"状态，由用户在图片上自行框选区域 */
  const startExtraRegion = (sourceRegion: DrawnRegion) => {
    setPendingQuestionNum(sourceRegion.question_number);
    setCurrentPage(sourceRegion.page_index);
    setSelectedId(null);
    message.info(`请在第${sourceRegion.page_index + 1}页上框选第${sourceRegion.question_number}题的额外区域，按 Esc 取消`);
  };

  // 当前页的区域按物理位置排列（用于画布上视觉参考）
  const sortedPageRegions = regions
    .filter((r) => r.page_index === currentPage)
    .sort((a, b) => a.y - b.y || a.x - b.x);

  // 全局列表按题号→绘制顺序排列（保持同题多区域的先后关系）
  const allRegionsSorted = [...regions].sort(
    (a, b) => a.question_number - b.question_number || a.draw_order - b.draw_order,
  );

  // ── Submit ──
  const handleSubmit = async () => {
    if (regions.length === 0) {
      message.warning('请至少绘制一个题目区域');
      return;
    }

    // 按题号→绘制顺序排列，保证同题多区域先后正确
    const sorted = [...regions].sort(
      (a, b) => a.question_number - b.question_number || a.draw_order - b.draw_order,
    );

    const payload: ManualRegion[] = sorted.map((r) => ({
      question_number: r.question_number,
      page_index: r.page_index,
      x: r.x,
      y: r.y,
      w: r.w,
      h: r.h,
      draw_order: r.draw_order,
      rotation: pageRotations[r.page_index] || 0,
    }));

    setSubmitting(true);
    try {
      if (prefillRegion) {
        // Adjust single question: first region is primary, the rest are extra regions
        // (dual-column / cross-page), merged vertically on the backend
        const [primary, ...extras] = payload;
        const adjPayload = {
          ...primary,
          rotation: primary.rotation || 0,
          extra_regions: extras.map((r) => ({
            page_index: r.page_index,
            x: r.x,
            y: r.y,
            w: r.w,
            h: r.h,
            rotation: r.rotation || 0,
          })),
        };
        await (
          await import('../services/questionService')
        ).questionService.adjustRegion(prefillRegion.question_id, adjPayload);
        message.success('题目区域已调整');
      } else {
        await assignmentService.manualSplit(assignmentId, payload);
        message.success(`手动切割完成，共 ${payload.length} 个区域`);
      }
      onSuccess();
    } catch (e: any) {
      message.error('操作失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'));
    } finally {
      setSubmitting(false);
    }
  };

  // ── Render ──
  return (
    <Modal
      title={prefillRegion ? '调整题目区域' : '手动切割题目'}
      open={visible}
      onCancel={onCancel}
      width="95vw"
      style={{ top: 20 }}
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={submitting} onClick={handleSubmit}>
            {prefillRegion ? '确认调整' : '确认切割'}
          </Button>
        </Space>
      }
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载源文件..." />
        </div>
      ) : !page ? (
        <Empty description="无法加载源文件" />
      ) : (
        <div style={{ display: 'flex', gap: 16, height: '75vh' }}>
          {/* ── Main canvas area ── */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            {/* Toolbar */}
            <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              {pendingQuestionNum !== null ? (
                <span style={{
                  color: '#fff', background: '#1890FF', padding: '4px 12px',
                  borderRadius: 4, fontWeight: 'bold', fontSize: 13,
                }}>
                  🔲 请框选第{pendingQuestionNum}题的额外区域（按 Esc 取消）
                </span>
              ) : (
                <span style={{ color: '#888' }}>
                  拖拽绘制矩形，选中后拖拽边缘调整大小，按 Delete 删除
                </span>
              )}
              {/* 旋转按钮组 */}
              <div style={{ borderLeft: '1px solid #d9d9d9', paddingLeft: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 12, color: '#888', marginRight: 4 }}>旋转：</span>
                <Button
                  size="small"
                  type={currentRotation === 90 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 270 ? 0 : (currentRotation + 90) as 0 | 90 | 180 | 270;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}
                  title="逆时针旋转90度"
                >
                  ↻ 90°
                </Button>
                <Button
                  size="small"
                  type={currentRotation === 180 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 180 ? 0 : 180;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}
                  title="旋转180度"
                >
                  180°
                </Button>
                <Button
                  size="small"
                  type={currentRotation === 270 ? 'primary' : 'default'}
                  onClick={() => {
                    const newRot = currentRotation === 0 ? 270 : ((currentRotation - 90 + 360) % 360) as 0 | 90 | 180 | 270;
                    setPageRotations((prev) => ({ ...prev, [currentPage]: newRot }));
                  }}
                  title="顺时针旋转90度"
                >
                  ↺ 90°
                </Button>
                {currentRotation !== 0 && (
                  <Button
                    size="small"
                    onClick={() => setPageRotations((prev) => ({ ...prev, [currentPage]: 0 }))}
                  >
                    重置
                  </Button>
                )}
              </div>
            </div>

            {/* Page nav */}
            {pages.length > 1 && (
              <div style={{ marginBottom: 8, textAlign: 'center' }}>
                <Space>
                  <Button
                    size="small"
                    icon={<LeftOutlined />}
                    disabled={currentPage === 0}
                    onClick={() => { setCurrentPage((p) => p - 1); setSelectedId(null); }}
                  />
                  <span>
                    第 {currentPage + 1} / {pages.length} 页
                  </span>
                  <Button
                    size="small"
                    icon={<RightOutlined />}
                    disabled={currentPage === pages.length - 1}
                    onClick={() => { setCurrentPage((p) => p + 1); setSelectedId(null); }}
                  />
                </Space>
              </div>
            )}

            {/* Canvas + Image */}
            <div
              ref={containerRef}
              style={{
                flex: 1,
                overflow: 'auto',
                border: '1px solid #d9d9d9',
                borderRadius: 4,
                background: '#f5f5f5',
                position: 'relative',
              }}
            >
              <div style={{ position: 'relative', display: 'inline-block' }}>
                <img
                  ref={imgRef}
                  src={displayUrl || page.image_url}
                  alt={`Page ${currentPage + 1}`}
                  style={{ display: 'block', maxWidth: '100%' }}
                  draggable={false}
                  onLoad={() => redraw()}
                />
                <canvas
                  ref={canvasRef}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    cursor: 'crosshair',
                  }}
                  onMouseDown={handleMouseDown}
                  onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp}
                  onMouseLeave={handleMouseUp}
                />
              </div>
            </div>
          </div>

          {/* ── Side panel: region list ── */}
          <div
            style={{
              width: 300,
              flexShrink: 0,
              border: '1px solid #d9d9d9',
              borderRadius: 4,
              padding: 12,
              overflow: 'auto',
              background: '#fafafa',
            }}
          >
            <h4 style={{ margin: '0 0 8px' }}>
              {prefillRegion ? '当前区域' : `已选区域 (${regions.length})`}
            </h4>
            {allRegionsSorted.length === 0 ? (
              <div style={{ color: '#999', fontSize: 13 }}>在图片上拖拽绘制题目区域</div>
            ) : (
              allRegionsSorted.map((r) => {
                const isSelected = r.id === selectedId;
                const isOnCurrentPage = r.page_index === currentPage;
                const color = COLORS[allRegionsSorted.indexOf(r) % COLORS.length];
                const hasContinuation = allRegionsSorted.filter(
                  (other) => other.question_number === r.question_number && other.id !== r.id
                ).length > 0;
                return (
                  <div
                    key={r.id}
                    onClick={() => {
                      setSelectedId(r.id);
                      if (!isOnCurrentPage) setCurrentPage(r.page_index);
                    }}
                    style={{
                      padding: 8,
                      marginBottom: 6,
                      borderRadius: 4,
                      border: `2px solid ${isSelected ? color : '#e8e8e8'}`,
                      background: isSelected ? color + '15' : '#fff',
                      cursor: 'pointer',
                      opacity: isOnCurrentPage ? 1 : 0.5,
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: 4,
                      }}
                    >
                      <Space size={4}>
                        <span
                          style={{
                            display: 'inline-block',
                            width: 10,
                            height: 10,
                            borderRadius: 2,
                            background: color,
                          }}
                        />
                        <strong>第</strong>
                        {prefillRegion ? (
                          <span>{r.question_number}</span>
                        ) : (
                          <InputNumber
                            size="small"
                            min={1}
                            max={200}
                            value={r.question_number}
                            onChange={(v) => updateQuestionNumber(r.id, v || 1)}
                            style={{ width: 55 }}
                            onClick={(e) => e.stopPropagation()}
                          />
                        )}
                        <strong>题</strong>
                      </Space>
                      {!prefillRegion ? (
                        <Space size={2}>
                          {/* + button for adding extra region (same-page dual-column or cross-page) */}
                          <Tooltip title="添加同题区域（双栏/跨页）">
                            <Button
                              size="small"
                              type="text"
                              icon={<PlusOutlined />}
                              onClick={(e) => { e.stopPropagation(); startExtraRegion(r); }}
                            />
                          </Tooltip>
                          <Popconfirm
                            title="确认删除此区域？"
                            onConfirm={(e) => { e?.stopPropagation(); deleteRegion(r.id); }}
                            onCancel={(e) => e?.stopPropagation()}
                          >
                            <Button
                              size="small"
                              danger
                              type="text"
                              icon={<DeleteOutlined />}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </Popconfirm>
                        </Space>
                      ) : (
                        <Space size={2}>
                          {/* 调整模式：+ 添加同题额外区域（A4双栏/跨页） */}
                          <Tooltip title="添加同题区域（双栏/跨页）">
                            <Button
                              size="small"
                              type="text"
                              icon={<PlusOutlined />}
                              onClick={(e) => { e.stopPropagation(); startExtraRegion(r); }}
                            />
                          </Tooltip>
                          {/* 至少保留一个区域 */}
                          {allRegionsSorted.length > 1 && (
                            <Popconfirm
                              title="确认删除此区域？"
                              onConfirm={(e) => { e?.stopPropagation(); deleteRegion(r.id); }}
                              onCancel={(e) => e?.stopPropagation()}
                            >
                              <Button
                                size="small"
                                danger
                                type="text"
                                icon={<DeleteOutlined />}
                                onClick={(e) => e.stopPropagation()}
                              />
                            </Popconfirm>
                          )}
                        </Space>
                      )}
                    </div>
                    <div style={{ fontSize: 11, color: '#888' }}>
                      第{r.page_index + 1}页 · {Math.round(r.w)}×{Math.round(r.h)}px
                      {hasContinuation && (
                        <span style={{ color: '#1890FF', marginLeft: 4 }}>（跨页）</span>
                      )}
                    </div>
                  </div>
                );
              })
            )}
            <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
              提示：选中切块后，拖动边缘/角上的白色方块可拉伸调整大小；拖动内部可移动位置。
              点击 "+" 进入框选模式，在图片上拖拽即可为该题添加额外区域（A4双栏左右分栏等）。
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
};

/** Get cursor style for resize handles */
function getResizeCursor(handle: HandleDir): string {
  const cursors: Record<HandleDir, string> = {
    nw: 'nwse-resize',
    n: 'ns-resize',
    ne: 'nesw-resize',
    e: 'ew-resize',
    se: 'nwse-resize',
    s: 'ns-resize',
    sw: 'nesw-resize',
    w: 'ew-resize',
    move: 'move',
  };
  return cursors[handle];
}

export default ManualSplitModal;
