// 预览子系统（v0.10.1 拆分自 panel.tsx，尺没动）：缓存 + 懒加载调度 + 取图 hook。
//
// —— 预览懒加载调度（轮 G-2）——
// 视口决定“看得见才拉”，全局并发尺限同时 2 张：单张预览是分段的串行调用，
// 几百格的图墙一次性开跑会踩挤插件子进程。拿不到 IntersectionObserver 就直接排队，
// 宁多拉不漏图。
//
// 预览取图（格子与聚焦卡共用同一套调度）：单张预览是分段的串行调用（宿主回包单帧上限≈4.56MiB，
// 实机超时钉的坑——DESIGN.md 陷阱 14）；循环有护栏，坏协议不许无限转；失败记空串，避免坏图重试风暴。
//
// v0.17.2 加了一档 kind="thumb"：**墙上的格子取 256px 缩略图，点开的聚焦卡才取原图**。
// 之前两者共用原图，实机账是官方区 190 张 ≈34.7MB（base64 后 ≈46MB）过控制通道、
// 并发 2 —— 排在后面的格子等过 30s 就是"加载图片超时"，每张一行 SDK 的 TRIGGER 日志
// 就是"日志刷屏"（当天 363 行里 313 行是 preview）。同一根因的两面，一起修。
// 代价（主人拍板接受）：动图在格子里只显示第一帧。

import { useEffect, useRef, useState } from "@neko/plugin-ui";
import { callAction } from "./shared";
import type { Surface } from "./shared";

// 预览缓存：`档位:id` -> dataUrl。失败记空串，避免每张坏图都重试一轮。
// 档位必须进键：同一张图的缩略图与原图是两个不同的 dataUrl，
// 共用一个键会让"先看过格子"的聚焦卡拿到糊图（或反过来让格子去等原图）。
const previewCache: Record<string, string> = {};

function cacheKey(kind: string, id: string): string {
  return `${kind}:${id}`;
}

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

export function useStickerPreview(
  surface: Surface,
  id: string,
  auto: boolean,
  kind: string = "full",
) {
  const key = cacheKey(kind, id);
  const [preview, setPreview] = useState<string>(previewCache[key] || "");
  const [loading, setLoading] = useState<boolean>(
    previewCache[key] === undefined,
  );
  const boxRef = useRef<any>(null);

  useEffect(() => {
    let alive = true;
    setPreview(previewCache[key] || "");
    setLoading(previewCache[key] === undefined);
    if (previewCache[key] !== undefined) {
      return undefined;
    }
    const load = async (): Promise<void> => {
      let dataUrl = "";
      try {
        let offset = 0;
        let mime = "";
        const parts: string[] = [];
        for (let guard = 0; guard < 16; guard += 1) {
          const args: Record<string, any> = { id: id, offset: offset };
          // 只有要缩略图时才带 kind：留空=原图，老形状一字不变（服务端 kind 缺省即走原路）。
          if (kind === "thumb") {
            args.kind = "thumb";
          }
          const result = await callAction(surface, "preview", args);
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
      previewCache[key] = dataUrl;
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
  }, [id, kind]);

  return { preview, loading, boxRef };
}
