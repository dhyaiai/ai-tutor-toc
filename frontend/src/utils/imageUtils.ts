/**
 * 图片工具：canvas 旋转生成。
 *
 * rotateImageDataUrl 用于试卷页/答案页的旋转预览（AnswerSplitModal 与 ManualSplitModal 共用），
 * 90/270 度时交换宽高，其余角度按原尺寸绘制，输出 PNG Data URL。
 */

/**
 * 在 canvas 中按给定角度（90 的倍数）旋转图片，返回 PNG Data URL。
 *
 * @param imageUrl 图片地址（同源或允许跨域读取）
 * @param rotation 旋转角度，0/90/180/270
 * @returns 旋转后的 PNG Data URL
 */
export function rotateImageDataUrl(imageUrl: string, rotation: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d")!;
      // 90/270 度旋转后宽高互换，其余角度保持原尺寸
      if (rotation === 90 || rotation === 270) {
        canvas.width = img.naturalHeight;
        canvas.height = img.naturalWidth;
      } else {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      // 以画布中心为原点旋转后再绘制，等效于围绕图片中心旋转
      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.rotate((rotation * Math.PI) / 180);
      ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
      resolve(canvas.toDataURL("image/png"));
    };
    img.onerror = () => reject(new Error("图片加载失败"));
    img.src = imageUrl;
  });
}
