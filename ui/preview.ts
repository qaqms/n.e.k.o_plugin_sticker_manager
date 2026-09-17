// 预览子系统（v0.10.1 拆分自 panel.tsx，尺没动）：缓存 + 懒加载调度 + 取图 hook。
//
// —— 预览懒加载调度（轮 G-2）——
// 视口决定“看得见才拉”，全局并发尺限同时 2 张：单张预览是分段的串行调用，
// 几百格的图墙一次性开跑会踩挤插件子进程。拿不到 IntersectionObserver 就直接排队，
// 宁多拉不漏图。
//
// 预览取图（格子与聚焦卡共用一把尺）：同一分段协议、同一缓存、同一并发限。
// 单张预览是分段的串行调用（宿主回包单帧上限≈4.56MiB，实机超时钉的坑——
// DESIGN.md 陷阱 14）；循环有护栏，坏协议不许无限转；失败记空串，避免坏图重试风暴。

import { useEffect, useRef, useState } from "@neko/plugin-ui";
import { callAction } from "./shared";
import type { Surface } from "./shared";

// 预览缓存：id -> dataUrl。失败记空串，避免每张坏图都重试一轮。
const previewCache: Record<string, string> = {};

const PREVIEW_CONCURRENCY = 2;
let previewActive = 0;
const previewWaiters: Array<() => Promise<void>> = [];

function pumpPreviewQueue() {
  while (previewActive < PREVIEW_CONCURRENCY && previewWaiters.length > 0) {
    const task = previewWaiters.shift();
    if (!task) {
      continue;
    }
    previewActive += 1;
    const settle = () => {
      previewActive -= 1;
      pumpPreviewQueue();
    };
    task().then(settle, settle);
  }
}

function queuePreview(task: () => Promise<void>) {
  previewWaiters.push(task);
  pumpPreviewQueue();
}

const tileHandlers: Map<any, () => void> = new Map();
let sharedObserver: any = null;

function observePreview(el: any, enter: () => void): () => void {
  const Ctor: any = (globalThis as any).IntersectionObserver;
  if (!Ctor || !el) {
    queuePreview(async () => {
      enter();
    });
    return () => {};
  }
  if (!sharedObserver) {
    sharedObserver = new Ctor(
      (entries: any[]) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) {
            return;
          }
          const handler = tileHandlers.get(entry.target);
          if (handler) {
            tileHandlers.delete(entry.target);
            sharedObserver.unobserve(entry.target);
            handler();
          }
        });
      },
      { rootMargin: "240px 0px" },
    );
  }
  tileHandlers.set(el, enter);
  sharedObserver.observe(el);
  return () => {
    tileHandlers.delete(el);
    if (sharedObserver) {
      sharedObserver.unobserve(el);
    }
  };
}

export function useStickerPreview(surface: Surface, id: string, auto: boolean) {
  const [preview, setPreview] = useState<string>(previewCache[id] || "");
  const [loading, setLoading] = useState<boolean>(
    previewCache[id] === undefined,
  );
  const boxRef = useRef<any>(null);

  useEffect(() => {
    let alive = true;
    setPreview(previewCache[id] || "");
    setLoading(previewCache[id] === undefined);
    if (previewCache[id] !== undefined) {
      return undefined;
    }
    const load = async (): Promise<void> => {
      let dataUrl = "";
      try {
        let offset = 0;
        let mime = "";
        const parts: string[] = [];
        for (let guard = 0; guard < 16; guard += 1) {
          const result = await callAction(surface, "preview", {
            id: id,
            offset: offset,
          });
          if (!result) {
            break;
          }
          mime = String(result.mime || mime);
          parts.push(String(result.chunk_base64 || ""));
          if (result.done) {
            dataUrl = `data:${mime};base64,${parts.join("")}`;
            break;
          }
          const next = Number(result.next_offset || 0);
          if (next <= offset) {
            break; // 协议不推进：当作坏图，不原地踏步
          }
          offset = next;
        }
      } catch {
        dataUrl = "";
      }
      previewCache[id] = dataUrl;
      if (alive) {
        setPreview(dataUrl);
        setLoading(false);
      }
    };
    if (auto) {
      // 聚焦卡开在眼前：直接排队拉，不必等视口观察。
      queuePreview(load);
      return () => {
        alive = false;
      };
    }
    // 墙上的格子：滚进视口（提前 240px）才排队；排队期间被卸载也无碍——load 幂等。
    const stop = observePreview(boxRef.current, () => {
      queuePreview(load);
    });
    return () => {
      alive = false;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  return { preview, loading, boxRef };
}
