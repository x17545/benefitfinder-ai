// F10 결과 이미지 저장. 조합 카드와 지금 고른 조합의 지원금 목록만 그림으로 만든다.
// 입력한 조건(나이·지역·부모님 정보 등)은 그림에 넣지 않는다.

export interface ResultImageModel {
  /** 예: 1년 기준 · 현재가치 */
  periodLabel: string;
  combos: { label: string; total: string; names: string; selected: boolean }[];
  listTitle: string;
  policies: { name: string; amount: string }[];
  createdAt: Date;
}

/** 한국 날짜 YYYY.MM.DD / YYYYMMDD */
function kstDate(date: Date): { dotted: string; compact: string } {
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const y = String(kst.getUTCFullYear());
  const m = String(kst.getUTCMonth() + 1).padStart(2, '0');
  const d = String(kst.getUTCDate()).padStart(2, '0');
  return { dotted: `${y}.${m}.${d}`, compact: `${y}${m}${d}` };
}

/** 파일 이름: 청년정책매니저_조합_20261026.png */
export function imageFileName(date: Date): string {
  return `청년정책매니저_조합_${kstDate(date).compact}.png`;
}

/** 그림 맨 아래 줄 */
export function imageFooter(date: Date): string {
  return `청년 정책 매니저 · AI 참고 결과 · 생성일 ${kstDate(date).dotted}`;
}

const WIDTH = 1080;
const PAD = 64;
const FONT = "'Pretendard Variable', Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif";

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value !== '' ? value : fallback;
}

/** 폭을 넘으면 여러 줄로 나눈다 */
function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  let line = '';
  for (const char of text) {
    const next = line + char;
    if (ctx.measureText(next).width > maxWidth && line !== '') {
      lines.push(line);
      line = char;
    } else {
      line = next;
    }
  }
  if (line !== '') {
    lines.push(line);
  }
  return lines;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

/** 그림을 그린다. 높이는 내용에 맞춘다 */
export function drawResultImage(model: ResultImageModel): HTMLCanvasElement {
  const colors = {
    bg: cssColor('--color-bg', '#dde6f5'),
    card: cssColor('--color-card', '#ffffff'),
    ink: cssColor('--color-ink', '#151b2b'),
    muted: cssColor('--color-muted', '#3e4e70'),
    primary: cssColor('--color-primary', '#1f2a44'),
    accent: cssColor('--color-accent', '#ffd45c'),
    line: cssColor('--color-line', '#d2def2'),
  };
  const inner = WIDTH - PAD * 2;

  // 1) 높이 재기용으로 한 번, 2) 실제로 한 번 그린다
  function paint(ctx: CanvasRenderingContext2D, draw: boolean): number {
    let y = PAD;
    const text = (value: string, size: number, weight: number, color: string, x = PAD): void => {
      ctx.font = `${String(weight)} ${String(size)}px ${FONT}`;
      if (draw) {
        ctx.fillStyle = color;
        ctx.fillText(value, x, y);
      }
    };

    ctx.textBaseline = 'top';
    text('내 지원금 조합', 44, 800, colors.ink);
    y += 64;
    text(model.periodLabel, 28, 500, colors.muted);
    y += 60;

    for (const combo of model.combos) {
      ctx.font = `500 28px ${FONT}`;
      const nameLines = wrap(ctx, combo.names, inner - 64);
      const boxHeight = 32 + 40 + 16 + 64 + 12 + nameLines.length * 40 + 24;
      if (draw) {
        ctx.fillStyle = colors.card;
        roundRect(ctx, PAD, y, inner, boxHeight, 32);
        ctx.fill();
        if (combo.selected) {
          ctx.lineWidth = 4;
          ctx.strokeStyle = colors.primary;
          ctx.stroke();
        }
      }
      let cy = y + 32;
      ctx.font = `800 26px ${FONT}`;
      const badgeWidth = ctx.measureText(combo.label).width + 28;
      if (draw) {
        ctx.fillStyle = combo.label === '추천 조합' ? colors.accent : colors.line;
        roundRect(ctx, PAD + 32, cy, badgeWidth, 40, 10);
        ctx.fill();
        ctx.fillStyle = colors.ink;
        ctx.fillText(combo.label, PAD + 46, cy + 6);
      }
      cy += 56;
      ctx.font = `800 52px ${FONT}`;
      if (draw) {
        ctx.fillStyle = colors.ink;
        ctx.fillText(combo.total, PAD + 32, cy);
      }
      cy += 76;
      ctx.font = `500 28px ${FONT}`;
      for (const line of nameLines) {
        if (draw) {
          ctx.fillStyle = colors.muted;
          ctx.fillText(line, PAD + 32, cy);
        }
        cy += 40;
      }
      y += boxHeight + 24;
    }

    y += 16;
    text(model.listTitle, 32, 800, colors.ink);
    y += 56;
    for (const policy of model.policies) {
      ctx.font = `700 30px ${FONT}`;
      const amountWidth = ctx.measureText(policy.amount).width;
      const nameLines = wrap(ctx, policy.name, inner - amountWidth - 48);
      if (draw) {
        ctx.fillStyle = colors.ink;
        nameLines.forEach((line, index) => {
          ctx.fillText(line, PAD, y + index * 42);
        });
        ctx.fillText(policy.amount, WIDTH - PAD - amountWidth, y);
      }
      y += nameLines.length * 42 + 20;
      if (draw) {
        ctx.fillStyle = colors.line;
        ctx.fillRect(PAD, y, inner, 2);
      }
      y += 22;
    }

    y += 24;
    text(imageFooter(model.createdAt), 24, 500, colors.muted);
    y += 40 + PAD;
    return y;
  }

  const measure = document.createElement('canvas').getContext('2d');
  const height = measure !== null ? paint(measure, false) : 1600;
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = Math.ceil(height);
  const ctx = canvas.getContext('2d');
  if (ctx !== null) {
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    paint(ctx, true);
  }
  return canvas;
}

/** PNG로 내려받는다. 성공하면 true */
export async function saveResultImage(model: ResultImageModel): Promise<boolean> {
  await document.fonts.ready;
  const canvas = drawResultImage(model);
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, 'image/png');
  });
  if (blob === null) {
    return false;
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = imageFileName(model.createdAt);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
  return true;
}
