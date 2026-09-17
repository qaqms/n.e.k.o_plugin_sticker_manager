// 面板共享层（v0.10.1 拆分）：类型 + 常量 + 纯函数——两处以上都要用的东西。
//
// 为什么单独成文件：`ui/panel.tsx` 曾长到 1924 行，骨架和每一块都焊死在同一文件里；
// 拆分后骨架留在 panel.tsx，可视模块在 ui/components/**，库卡的动作状态在
// library_model.ts，预览子系统在 preview.ts——这里只放**两边都要用**的东西
// （对偶性纪律：两处需要的逻辑只写一遍）。
//
// hosted-tsx 约束照旧适用于本文件：只 import `@neko/plugin-ui` 与相对路径；
// 只用具名导出；门面永远写成 `props.xxx.api` / `surface.api` 的成员访问形态，
// 绝不把它赋给别的标识符（检查器是文本级的，见 DESIGN.md 陷阱 7）。

import type { PluginSurfaceProps } from "@neko/plugin-ui"

export type Translate = (key: string, params?: Record<string, any>) => string

export type StickerRow = {
  id: string;
  file?: string;
  desc?: string;
  tags?: string[];
  disabled?: boolean;
  added_at?: number;
  use_count?: number;
  last_used_at?: number;
  group?: string;
  caption?: string;
  visible_text?: string;
};

export type UsageRow = {
  at?: number;
  id?: string;
  lanlan?: string;
  source?: string;
  ok?: boolean;
  code?: string;
};

export type AwarenessState = {
  status?: string;
  target?: string;
  last_inject_at?: number | null;
  min_next_wait_sec?: number;
};

export type GroupInfo = {
  name: string;
  count?: number;
  desc?: string;
};

// 轮 I（分类优先）：浏览与收图都以分类为单位——一个分类一个区块，
// 收图入口在区块头上（目标分类就是这一块）；`total` 是服务端张数（搜索会筛掉行，
// 但删分类的确认必须摊真实的数）。
export type Section = {
  key: string;
  name: string;
  desc: string;
  rows: StickerRow[];
  total: number;
  // 真正的分类名（只有它能进服务端参数）：`key` 另当 React 键与“未分组”哨兵，
  // 拿它当组名会让一叠语义建在一个字面量上（主人真给分类起名 `__none__` 就撞）。
  group: string;
  editable: boolean;
};

export type State = {
  enabled?: boolean;
  lanlan?: string;
  counts?: {
    total?: number;
    enabled?: number;
    sent_total?: number;
    groups?: number;
  };
  stickers?: StickerRow[];
  groups?: GroupInfo[];
  usage?: UsageRow[];
  inbox?: { pending?: number; path?: string };
  awareness?: AwarenessState;
  config?: {
    cooldown_sec?: number;
    inline_max_bytes?: number;
    catalog_limit_for_model?: number;
    recent_dedup_count?: number;
    probability?: number;
    probability_reuse_sec?: number;
    awareness_enabled?: boolean;
    awareness_interval_sec?: number;
  };
  error_code?: string;
};

export type Surface = PluginSurfaceProps<State>;

// 批量通道单次上限：防一次拖几百张把插件子进程堆满 base64；超出部分如实报数。
export const MAX_BATCH_FILES = 64;
// 与 core.catalog.MAX_STICKER_BYTES 同数（iframe 碰不到 Python，跨运行时重复）。
export const MAX_STICKER_BYTES = 8 * 1024 * 1024;

// 长任务（导入/导出/套图包收尾）服务端 timeout=120s，但宿主桥接客户端默认只等 30s
//（runtime.js 实测）——不对齐的话大包导入服务端在继续、面板先报假失败。这些调用显式传 opts。
export const LONG_CALL = { timeoutMs: 120000 };

// 后端 Err 抛出的 message 里抓稳定 ASCII 码（^[a-z][a-z0-9_]*$，DESIGN.md 错误码契约）；
// 抓不到就原样直出（宿主/网络错误的原文比编一个码诚实）。
export function extractCode(raw: string): string {
  const m = raw.match(/[a-z][a-z0-9_]*/);
  return m ? m[0] : raw;
}

export function readAsDataUrl(file: any): Promise<string> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(String(reader.result || ""));
    };
    reader.onerror = () => {
      resolve("");
    };
    reader.readAsDataURL(file);
  });
}

export function formatTime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

export function dataUrlToBase64(value: string): string {
  const comma = value.indexOf(",");
  return comma >= 0 ? value.slice(comma + 1) : value;
}

// 动作调用返回信封 `{plugin_id, action_id, result}`，真正的返回值在 `.result`。
export async function callAction(
  surface: Surface,
  actionId: string,
  args: Record<string, unknown>,
  options?: { timeoutMs?: number },
): Promise<any> {
  const envelope = await surface.api.call(actionId, args, options);
  return envelope ? envelope.result : null;
}

// 轮 I：逐图归类只能从已有分类里挑（要新名字请先「新建分类」）。
// 为什么不用输入框：手打一个新名字会静默立一个没说明的隐式分类，
// 把“先分类、后收图”的模型戳穿；选项首位是空值 = 未分组（批量也能把图迁出来）。
export function categoryOptions(surface: Surface, t: Translate): any[] {
  const listed: GroupInfo[] = (surface.state && surface.state.groups) || [];
  const options: any[] = [
    {
      value: "",
      label: t("panel.group.none", { defaultValue: "未分组" }),
    },
  ];
  listed.forEach((info: GroupInfo) => {
    const name = String((info && info.name) || "");
    if (name) {
      options.push({ value: name, label: name });
    }
  });
  return options;
}

// 搜索语义：组名/组说明命中→整组都在；否则只留命中的图。
// **轮 I 反转 v0.8.0 的“空区块不出现”**：零张的分类必须显示（否则建完就消失，
// 等于没建）——但仅限“这个分类本来就没图”，搜索筛空的有图分类仍不出现。
export function buildSections(
  stickers: StickerRow[],
  groups: GroupInfo[],
  term: string,
  t: Translate,
): Section[] {
  const rowMatches = (row: StickerRow): boolean => {
    if (!term) {
      return true;
    }
    const haystack = [
      row.id,
      row.desc || "",
      (row.tags || []).join(" "),
      row.group || "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.indexOf(term) >= 0;
  };
  const sections: Section[] = [];
  groups.forEach((info: GroupInfo) => {
    const catHit =
      !term ||
      `${info.name} ${info.desc || ""}`.toLowerCase().indexOf(term) >= 0;
    const rows = stickers.filter(
      (row) => row.group === info.name && (catHit || rowMatches(row)),
    );
    const total = info.count ?? 0;
    if (rows.length || (catHit && !total)) {
      sections.push({
        key: info.name,
        name: info.name,
        desc: info.desc || "",
        rows,
        total,
        group: info.name,
        editable: true,
      });
    }
  });
  const ungrouped = stickers.filter((row) => !row.group && rowMatches(row));
  if (ungrouped.length) {
    sections.push({
      key: "__none__",
      name: t("panel.group.none", { defaultValue: "未分组" }),
      desc: "",
      rows: ungrouped,
      total: ungrouped.length,
      group: "",
      editable: false,
    });
  }
  return sections;
}
