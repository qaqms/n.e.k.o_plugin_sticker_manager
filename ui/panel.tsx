// Hosted TSX 面板：表情包管理。只从 `@neko/plugin-ui` 导入，业务逻辑全在 Python 侧。
//
// 契约要点（照 plugin/sdk/hosted-ui/index.d.ts 的精确签名写）：
// - 动作调用返回信封 `{plugin_id, action_id, result}`，真正的返回值在 `.result`。
// - 只能调被 @ui.action 暴露过的入口；检查器是**文本级**规则：
//   一律写完整的 `props.xxx.api` 成员访问，绝不出现裸 `api` 标识符（含参数名与别名）。
// - 缩略图懒加载：context 只带元数据，预览字节由 `preview` 动作按 id 现取，
//   模块级缓存（同一 iframe 生命周期内不重复取图；坏图记空串，不重试风暴）。
import {
  Alert,
  Button,
  Card,
  DataTable,
  Divider,
  EmptyState,
  Field,
  Input,
  Inline,
  KeyValue,
  Page,
  ScrollArea,
  Select,
  Stack,
  StatusBadge,
  Switch,
  Text,
  useConfirm,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui";
import type { PluginSurfaceProps } from "@neko/plugin-ui";

type StickerRow = {
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

type UsageRow = {
  at?: number;
  id?: string;
  lanlan?: string;
  source?: string;
  ok?: boolean;
  code?: string;
};

type AwarenessState = {
  status?: string;
  target?: string;
  last_inject_at?: number | null;
  min_next_wait_sec?: number;
};

type GroupInfo = {
  name: string;
  count?: number;
  desc?: string;
};

// 轮 I（分类优先）：浏览与收图都以分类为单位——一个分类一个区块，
// 收图入口在区块头上（目标分类就是这一块）；`total` 是服务端张数（搜索会筛掉行，
// 但删分类的确认必须摊真实的数）。
type Section = {
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

type State = {
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

type Surface = PluginSurfaceProps<State>;

// 预览缓存：id -> dataUrl。失败记空串，避免每张坏图都重试一轮。
const previewCache: Record<string, string> = {};

// —— 预览懒加载调度（轮 G-2）——
// 视口决定“看得见才拉”，全局并发尺限同时 2 张：单张预览是分段的串行调用，
// 几百格的图墙一次性开跑会踩挤插件子进程。拿不到 IntersectionObserver 就直接排队，
// 宁多拉不漏图。
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

// 后端 Err 抛出的 message 里抓稳定 ASCII 码（^[a-z][a-z0-9_]*$，DESIGN.md 错误码契约）；
// 抓不到就原样直出（宿主/网络错误的原文比编一个码诚实）。
function extractCode(raw: string): string {
  const m = raw.match(/[a-z][a-z0-9_]*/);
  return m ? m[0] : raw;
}

// 批量通道单次上限：防一次拖几百张把插件子进程堆满 base64；超出部分如实报数。
const MAX_BATCH_FILES = 64;
// 与 core.catalog.MAX_STICKER_BYTES 同数（iframe 碰不到 Python，跨运行时重复）。
const MAX_STICKER_BYTES = 8 * 1024 * 1024;

function readAsDataUrl(file: any): Promise<string> {
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

function formatTime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

function dataUrlToBase64(value: string): string {
  const comma = value.indexOf(",");
  return comma >= 0 ? value.slice(comma + 1) : value;
}

// 长任务（导入/导出/套图包收尾）服务端 timeout=120s，但宿主桥接客户端默认只等 30s
//（runtime.js 实测）——不对齐的话大包导入服务端在继续、面板先报假失败。这些调用显式传 opts。
const LONG_CALL = { timeoutMs: 120000 };

async function callAction(
  surface: Surface,
  actionId: string,
  args: Record<string, unknown>,
  options?: { timeoutMs?: number },
): Promise<any> {
  const envelope = await surface.api.call(actionId, args, options);
  return envelope ? envelope.result : null;
}

// 预览取图（格子与聚焦卡共用一把尺）：同一分段协议、同一缓存、同一并发限。
// 单张预览是分段的串行调用（宿主回包单帧上限≈4.56MiB，实机超时钉的坑）；
// 循环有护栏，坏协议不许无限转；失败记空串，避免坏图重试风暴。
function useStickerPreview(surface: Surface, id: string, auto: boolean) {
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

// 格子：纯图方块（v0.9.1 修正：scale-down 只缩不放——160px 小图塞 360px 大格
// 被放大糊脸，就是"拉伸感"的真凶）。左上角勾选框是唯一的体外控件；
// 点击=在墙上方展开聚焦卡（弹窗形态见 FocusCard 注释：kit Modal 在宿主里是平台级残废）。
function StickerTile(props: {
  key?: string;
  row: StickerRow;
  surface: Surface;
  selected?: boolean;
  onToggleSelect?: () => void;
  onOpen?: () => void;
}) {
  const row = props.row;
  const t = props.surface.t;
  const { preview, loading, boxRef } = useStickerPreview(
    props.surface,
    row.id,
    false,
  );

  const tileBox = (extra: Record<string, unknown>) => {
    const base: Record<string, unknown> = {
      position: "relative",
      width: "100%",
      aspectRatio: "1 / 1",
      borderRadius: 8,
      overflow: "hidden",
      cursor: "pointer",
      border: "1px solid rgba(128, 128, 128, 0.35)",
      background: "rgba(128, 128, 128, 0.10)",
    };
    return Object.assign(base, extra);
  };
  const centerBox: Record<string, unknown> = {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 12,
    opacity: 0.75,
  };

  return (
    <div
      ref={boxRef}
      style={tileBox(
        props.selected ? { border: "2px solid rgba(80, 160, 255, 0.9)" } : {},
      )}
      onClick={() => {
        if (props.onOpen) {
          props.onOpen();
        }
      }}
    >
      {preview ? (
        <img
          src={preview}
          alt={row.desc || row.id}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "scale-down",
            display: "block",
          }}
        />
      ) : null}
      {!preview && loading ? (
        <div style={centerBox}>
          {t("panel.tile.loading", { defaultValue: "加载中…" })}
        </div>
      ) : null}
      {!preview && !loading ? (
        <div style={centerBox}>
          {t("panel.tile.failed", { defaultValue: "图不可用" })}
        </div>
      ) : null}
      {row.disabled ? (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(20, 20, 20, 0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <StatusBadge
            tone="warning"
            label={t("panel.badge.disabled", { defaultValue: "已禁用" })}
          />
        </div>
      ) : null}
      <div
        style={{
          position: "absolute",
          top: 3,
          left: 3,
          background: "rgba(255, 255, 255, 0.85)",
          borderRadius: 4,
          padding: "1px 3px",
          lineHeight: 1,
        }}
        onClick={(event: any) => {
          event.stopPropagation();
          if (props.onToggleSelect) {
            props.onToggleSelect();
          }
        }}
      >
        <input type="checkbox" checked={!!props.selected} readOnly />
      </div>
    </div>
  );
}

// 聚焦卡（v0.9.1）：点格子后在墙上方就地展开。为什么不用弹窗——kit 的
// .neko-page/.neko-card 都挂着 animation ... both，关键帧终帧 transform:translateY(0)
// 被永久保留，任何 position:fixed 后代都被收进祖先包含块、再被 Card 的 overflow:hidden
// 裁切（实机截图钉的：遮罩只压暗卡片区域、弹窗底部直接裁没）。插件侧修不动平台 CSS，
// 所以这里零 overlay、零 fixed：大图（不放大、原尺寸封顶）+ 全属性 + 动作排，
// 「编辑」在同一张卡里就地切表单——看做分离的语义不变，载体换硬了。
// 轮 I：分类是**显式对象**——逐图归类只能从已有分类里挑（要新名字请先「新建分类」）。
// 为什么不用输入框：手打一个新名字会静默立一个没说明的隐式分类，
// 把“先分类、后收图”的模型戳穿；选项首位是空值 = 未分组（批量也能把图迁出来）。
function categoryOptions(surface: Surface, translate: any): any[] {
  const listed: GroupInfo[] = (surface.state && surface.state.groups) || [];
  const options: any[] = [
    {
      value: "",
      label: translate("panel.group.none", { defaultValue: "未分组" }),
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

function FocusCard(props: {
  key?: string;
  surface: Surface;
  row: StickerRow;
  onExit: () => void;
}) {
  const row = props.row;
  const surface = props.surface;
  const t = surface.t;
  const confirm = useConfirm();
  const { preview, loading, boxRef } = useStickerPreview(surface, row.id, true);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<boolean>(false);
  const [editDesc, setEditDesc] = useState<string>(row.desc || "");
  const [editTags, setEditTags] = useState<string>((row.tags || []).join(","));
  const [editGroup, setEditGroup] = useState<string>(row.group || "");
  const [editCaption, setEditCaption] = useState<string>(row.caption || "");
  const [editVisible, setEditVisible] = useState<string>(
    row.visible_text || "",
  );

  const run = async (actionId: string, args: Record<string, unknown>) => {
    setNote("");
    try {
      await callAction(surface, actionId, args);
      await surface.api.refresh();
    } catch (error) {
      // 错误码是稳定 ASCII（契约见 DESIGN.md）：能翻的翻，翻不动直出码。
      // 只 console.warn 等于静默吞掉——send_cooldown 这类实机反馈要求看得见。
      console.warn("sticker_manager action failed", actionId, error);
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setNote(t(`panel.error.${code}`, { defaultValue: code }));
    }
  };

  const remove = async () => {
    const answer = await confirm({
      title: t("panel.remove.title", { defaultValue: "删除表情包" }),
      message: t("panel.remove.message", {
        defaultValue: "这张图和它的记录都会被删掉，不可恢复。",
      }),
      tone: "danger",
    });
    if (answer) {
      await run("remove", { id: row.id });
      props.onExit();
    }
  };

  return (
    <Card
      title={
        (t("panel.focus.title", { defaultValue: "表情详情" }) as string) +
        " · " +
        (row.caption || row.desc || row.id)
      }
    >
      <Stack gap={10}>
        {editing ? (
          <Stack gap={8}>
            <Field
              label={t("panel.edit.desc", {
                defaultValue: "描述（面板里的短标签）",
              })}
            >
              <Input
                value={editDesc}
                onChange={setEditDesc}
                placeholder={t("panel.edit.desc.ph", {
                  defaultValue: "一句话说明图里在干什么",
                })}
              />
            </Field>
            <Field
              label={t("panel.edit.caption", {
                defaultValue: "梗义（她选图时看到的正文；留空=清掉标注）",
              })}
            >
              <Input
                value={editCaption}
                onChange={setEditCaption}
                placeholder={t("panel.edit.caption.ph", {
                  defaultValue: "例：被催了很久之后终于交差，得意中带点解脱",
                })}
              />
            </Field>
            <Field
              label={t("panel.edit.visible", {
                defaultValue: "图内原文（只帮她搜到，不上目录）",
              })}
            >
              <Input
                value={editVisible}
                onChange={setEditVisible}
                placeholder={t("panel.edit.visible.ph", {
                  defaultValue: "例：就这？",
                })}
              />
            </Field>
            <Field
              label={t("panel.edit.tags", { defaultValue: "标签（逗号分隔）" })}
            >
              <Input
                value={editTags}
                onChange={setEditTags}
                placeholder="开心, 猫"
              />
            </Field>
            <Field
              label={t("panel.edit.group", {
                defaultValue:
                  "套图分类（只能选已有的；要新名字先点「新建分类」）",
              })}
            >
              <Select
                value={editGroup}
                options={categoryOptions(surface, t)}
                onChange={(next: any) => {
                  setEditGroup(
                    String(next === undefined || next === null ? "" : next),
                  );
                }}
              />
            </Field>
            <Inline gap={6}>
              <Button
                tone="primary"
                onClick={() => {
                  setEditing(false);
                  run("update", {
                    id: row.id,
                    desc: editDesc,
                    tags: editTags,
                    group: editGroup,
                    caption: editCaption,
                    visible_text: editVisible,
                  });
                }}
              >
                {t("panel.edit.save", { defaultValue: "保存" })}
              </Button>
              <Button
                tone="default"
                onClick={() => {
                  setEditing(false);
                }}
              >
                {t("panel.edit.cancel", { defaultValue: "取消" })}
              </Button>
            </Inline>
          </Stack>
        ) : (
          <Stack gap={8}>
            <Inline gap={12} align="start" wrap>
              <div
                ref={boxRef}
                style={{
                  minWidth: 140,
                  minHeight: 140,
                  maxWidth: 420,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: "rgba(128, 128, 128, 0.10)",
                  borderRadius: 8,
                  overflow: "hidden",
                }}
              >
                {preview ? (
                  <img
                    src={preview}
                    alt={row.desc || row.id}
                    style={{ maxWidth: 420, maxHeight: 380, display: "block" }}
                  />
                ) : (
                  <Text>
                    {loading
                      ? t("panel.tile.loading", { defaultValue: "加载中…" })
                      : t("panel.tile.failed", { defaultValue: "图不可用" })}
                  </Text>
                )}
              </div>
              <div style={{ flex: 1, minWidth: 240 }}>
                <KeyValue
                  items={[
                    { key: "id", label: "id", value: row.id },
                    {
                      key: "desc",
                      label: t("panel.detail.desc", { defaultValue: "描述" }),
                      value: row.desc || "—",
                    },
                    {
                      key: "caption",
                      label: t("panel.detail.caption", {
                        defaultValue: "梗义",
                      }),
                      value: row.caption || "—",
                    },
                    {
                      key: "visible",
                      label: t("panel.detail.visible", {
                        defaultValue: "图内原文",
                      }),
                      value: row.visible_text || "—",
                    },
                    {
                      key: "group",
                      label: t("panel.detail.group", { defaultValue: "分组" }),
                      value: row.group || "—",
                    },
                    {
                      key: "tags",
                      label: t("panel.detail.tags", { defaultValue: "标签" }),
                      value: (row.tags || []).join(" / ") || "—",
                    },
                    {
                      key: "uses",
                      label: t("panel.thumb.uses", {
                        defaultValue: "发出次数",
                      }),
                      value: String(row.use_count || 0),
                    },
                    {
                      key: "last",
                      label: t("panel.thumb.last", {
                        defaultValue: "最近发出",
                      }),
                      value: formatTime(row.last_used_at),
                    },
                    {
                      key: "added",
                      label: t("panel.detail.added", { defaultValue: "入库" }),
                      value: formatTime(row.added_at),
                    },
                  ]}
                />
              </div>
            </Inline>
            <Inline gap={6} wrap>
              <Button
                tone="success"
                onClick={() => {
                  run("send", { id: row.id });
                }}
              >
                {t("panel.action.send", { defaultValue: "发到聊天" })}
              </Button>
              <Button
                tone="default"
                onClick={() => {
                  setEditing(true);
                }}
              >
                {t("panel.action.edit", { defaultValue: "编辑" })}
              </Button>
              <Button
                tone="warning"
                onClick={() => {
                  run("update", { id: row.id, disabled: !row.disabled });
                }}
              >
                {row.disabled
                  ? t("panel.action.enable", { defaultValue: "恢复启用" })
                  : t("panel.action.disable", { defaultValue: "禁用" })}
              </Button>
              <Button
                tone="danger"
                onClick={() => {
                  remove();
                }}
              >
                {t("panel.action.remove", { defaultValue: "删除" })}
              </Button>
              <Button
                tone="default"
                onClick={() => {
                  props.onExit();
                }}
              >
                {t("panel.focus.back", { defaultValue: "返回墙" })}
              </Button>
            </Inline>
            {note ? <Alert tone="danger" message={note} /> : null}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

export default function Panel(props: Surface) {
  const state = props.state || {};
  const t = props.t;
  const [query, setQuery] = useState("");
  const [libraryNote, setLibraryNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [batchTags, setBatchTags] = useState("");
  const [batchGroup, setBatchGroup] = useState("");
  // 轮 G：分类说明改为就地编辑——同一时刻只开一个区块的编辑行。
  const [descEditing, setDescEditing] = useState("");
  const [descDraft, setDescDraft] = useState("");
  // 轮 I（分类优先）：新建分类也是就地展开（同一时刻只开一个），
  // 不用覆盖层弹窗——理由见陷阱 20（kit Modal 在宿主 iframe 里平台级残废）。
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [collectBusy, setCollectBusy] = useState(false);
  // v0.9.1：详情载体是聚焦卡（库卡顶部就地展开），不是弹窗——kit Modal 在宿主里平台级残废。
  const [focus, setFocus] = useState("");
  const confirm = useConfirm();
  const zipInputRef = useRef<any>(null);
  // 轮 I：全库共用**一个**隐藏图片输入框，目标分类走 ref 而不是 state——
  // “点区块头收图→click()→onChange” 三步里 onChange 的闭包可能抓到旧 state，
  // ref 赋值当场生效，不靠重渲染传参。
  const imgInputRef = useRef<any>(null);
  const collectTargetRef = useRef<string>("");
  const [awarenessNote, setAwarenessNote] = useState("");
  const stickers = state.stickers || [];
  const groups = state.groups || [];

  const exportPack = async () => {
    setLibraryNote("");
    try {
      const result = await callAction(props, "export_pack", {}, LONG_CALL);
      if (result) {
        setLibraryNote(
          t("panel.export.done", {
            exported: result.exported ?? 0,
            skipped: result.skipped ?? 0,
            file: result.file ?? "",
            defaultValue: "已导出 {exported} 张（跳过 {skipped}）：{file}",
          }),
        );
      }
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const ping = async () => {
    setAwarenessNote("");
    try {
      const result = await callAction(props, "awareness_now", {});
      if (result) {
        setAwarenessNote(
          t(`panel.awareness.status.${String(result.status || "")}`, {
            defaultValue: String(result.status || ""),
          }),
        );
      }
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setAwarenessNote(t(`panel.error.${code}`, { defaultValue: code }));
    }
  };

  const repair = async () => {
    setLibraryNote("");
    try {
      const result = await callAction(props, "repair", {});
      if (result) {
        setLibraryNote(
          t("panel.repair.done", {
            entries: result.removed_entries ?? 0,
            files: result.purged_files ?? 0,
            hashes: result.backfilled_hashes ?? 0,
            defaultValue:
              "体检完成：清理条目 {entries}、孤儿文件 {files}、回填指纹 {hashes}",
          }),
        );
      }
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const importInbox = async () => {
    setLibraryNote("");
    try {
      const result = await callAction(props, "import_inbox", {}, LONG_CALL);
      if (result) {
        setLibraryNote(
          t("panel.inbox.done", {
            imported: result.imported ?? 0,
            duplicates: result.duplicates ?? 0,
            rejected: result.rejected ?? 0,
            failed: result.failed ?? 0,
            defaultValue:
              "导入完成：收进 {imported}、重复跳过 {duplicates}、坏图/超限 {rejected}、失败 {failed}",
          }),
        );
      }
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 套图包直传（v0.6.0）：选择 .zip → 分块上传 → 服务端同一把尺导入。
  // 分块大小由服务端 start 回包定（与预览共用同一条 ZMQ 帧尺），面板不硬编码。
  const readFileChunk = (blob: any): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const text = String(reader.result || "");
        const comma = text.indexOf(",");
        resolve(comma >= 0 ? text.slice(comma + 1) : "");
      };
      reader.onerror = () => reject(new Error("read_failed"));
      reader.readAsDataURL(blob);
    });

  const importZip = async (file: any) => {
    if (uploading) {
      return;
    }
    setUploading(true);
    setLibraryNote("");
    try {
      const start = await callAction(props, "import_upload_start", {
        name: file.name,
        size: file.size,
      });
      const sid = String((start && start.session) || "");
      const chunkBytes = Number((start && start.chunk_bytes) || 3145728);
      const total = Math.max(1, Math.ceil(Number(file.size) / chunkBytes));
      let seq = 0;
      for (let offset = 0; offset < Number(file.size); offset += chunkBytes) {
        const chunk = await readFileChunk(
          file.slice(offset, offset + chunkBytes),
        );
        await callAction(props, "import_upload_chunk", {
          session: sid,
          seq,
          data_base64: chunk,
        });
        seq += 1;
        setLibraryNote(
          t("panel.upload.progress", {
            done: seq,
            total,
            defaultValue: "上传中 {done}/{total}…",
          }),
        );
      }
      const fin = await callAction(
        props,
        "import_upload_finish",
        { session: sid },
        LONG_CALL,
      );
      if (fin) {
        setLibraryNote(
          t("panel.upload.done", {
            imported: fin.imported ?? 0,
            duplicates: fin.duplicates ?? 0,
            rejected: fin.rejected ?? 0,
            failed: fin.failed ?? 0,
            defaultValue:
              "套图包导入完成：收进 {imported}、重复跳过 {duplicates}、坏图/超限 {rejected}、失败 {failed}",
          }),
        );
      }
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
    setUploading(false);
  };

  // 轮 G：分组说明编辑（“分类即 prompt”的一句维护入口），就地挂在区块头上。
  const saveGroupDesc = async () => {
    if (!descEditing) {
      return;
    }
    setLibraryNote("");
    try {
      await callAction(props, "group_set_desc", {
        group: descEditing,
        desc: descDraft.trim(),
      });
      setLibraryNote(
        t("panel.group.desc_saved", { defaultValue: "分组说明已更新" }),
      );
      setDescEditing("");
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 轮 I：分类优先——先立分类（名字必填、说明可空），再往分类里收图。
  // 逐图字段（描述/梗义/标签）不在收图时问：收完点开图在聚焦卡里补。
  const createCategory = async () => {
    const name = String(newName || "").trim();
    if (!name) {
      setLibraryNote(
        t("panel.group.name_required", { defaultValue: "先给分类起个名字" }),
      );
      return;
    }
    setLibraryNote("");
    try {
      await callAction(props, "group_create", {
        group: name,
        desc: String(newDesc || "").trim(),
      });
      setLibraryNote(
        t("panel.group.created", {
          name: name,
          defaultValue: "分类「{name}」已建好——点它块头的「收图」往里塞表情。",
        }),
      );
      setNewName("");
      setNewDesc("");
      setCreating(false);
      // 防“建完了但屏幕上看不到”：搜索词会把零张新区块筛掉（它不命中分类名），
      // 刚建的分类应当当场就在眼前。
      setQuery("");
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 删分类 = 连带删它里的图（主人拍板 1C）：确认里先把精确张数摊开。
  const removeCategory = async (section: Section) => {
    const answer = await confirm({
      title: t("panel.group.delete_title", { defaultValue: "删分类" }),
      message: section.total
        ? t("panel.group.delete_message", {
            name: section.name,
            count: section.total,
            defaultValue:
              "将拆掉分类「{name}」，连带删它里的 {count} 张图与文件，不可恢复。",
          })
        : t("panel.group.delete_message_empty", {
            name: section.name,
            defaultValue:
              "删掉空分类「{name}」？（它里面对她不可见，不会影哿发图）",
          }),
      tone: "danger",
    });
    if (!answer) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(
        props,
        "group_remove",
        { group: section.group },
        LONG_CALL,
      );
      setLibraryNote(
        t("panel.group.delete_done", {
          name: section.name,
          count: (result && result.removed) ?? 0,
          defaultValue: "已删分类「{name}」（连带 {count} 张图）",
        }),
      );
      setSelected([]);
      // 聚焦卡不需要手动收：删掉的图不在 state.stickers 里，focusRow 自然为空。
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 轮 I（2A）：收图分散到区块头。目标分类走 ref 传递（见 collectTargetRef 注释）；
  // 逐张走已测的 add 通道（魔数/查重/体积尺全复用）。
  const collectInto = (group: string) => {
    collectTargetRef.current = group;
    if (imgInputRef.current) {
      imgInputRef.current.click();
    }
  };

  const importFiles = async (files: any, group: string) => {
    const list: any[] = Array.from(files || []);
    if (!list.length || collectBusy) {
      return;
    }
    const taken = list.slice(0, MAX_BATCH_FILES);
    setCollectBusy(true);
    setLibraryNote("");
    let ok = 0;
    let dup = 0;
    let big = 0;
    let fail = 0;
    for (let i = 0; i < taken.length; i += 1) {
      const file = taken[i];
      if (Number(file.size) > MAX_STICKER_BYTES) {
        big += 1;
      } else {
        const b64 = dataUrlToBase64(await readAsDataUrl(file));
        if (b64) {
          try {
            // 收图不问逐图字段：desc 交空串，目录正文按轮 F 那把尺回落到分类说明。
            // （不再拿文件名当描述：外部包的哈希名会把自已在目录里压到分类说明头上。）
            const result = await callAction(props, "add", {
              data_base64: b64,
              desc: "",
              group: group,
            });
            if (result && result.note === "sticker_added") {
              ok += 1;
            } else {
              fail += 1;
            }
          } catch (error) {
            const raw =
              error instanceof Error ? error.message : String(error ?? "");
            if (extractCode(raw) === "duplicate_image") {
              dup += 1;
            } else {
              fail += 1;
            }
          }
        } else {
          fail += 1; // 空 base64：读不出内容，计失败
        }
      }
      setLibraryNote(
        t("panel.collect.busy", {
          done: i + 1,
          total: taken.length,
          defaultValue: "收藏中 {done}/{total}…",
        }),
      );
    }
    setCollectBusy(false);
    await props.api.refresh();
    const extra = list.length - taken.length;
    setLibraryNote(
      t("panel.collect.done", {
        ok: ok,
        dup: dup,
        big: big,
        fail: fail,
        defaultValue:
          "已收 {ok} · 重复跳过 {dup} · 超限略过 {big} · 失败 {fail}",
      }) +
        (group
          ? t("panel.collect.into", {
              name: group,
              defaultValue: "（进「{name}」）",
            })
          : "") +
        (extra > 0
          ? t("panel.batch.more", {
              extra: extra,
              defaultValue: "；本次未处理 {extra} 张",
            })
          : ""),
    );
  };

  const toggleSelected = (id: string) => {
    setSelected((previous: string[]) =>
      previous.indexOf(id) >= 0
        ? previous.filter((x) => x !== id)
        : previous.concat(id),
    );
  };

  // 全选本组：没全选过→补齐；已全选→再点取消本组选择。
  const selectSection = (rows: StickerRow[]) => {
    const ids = rows.map((row) => row.id);
    setSelected((previous: string[]) => {
      const missing = ids.filter((id) => previous.indexOf(id) < 0);
      return missing.length
        ? previous.concat(missing)
        : previous.filter((id) => ids.indexOf(id) < 0);
    });
  };

  const runBatch = async (patch: Record<string, unknown>) => {
    if (!selected.length) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(props, "batch_update", {
        ids: selected,
        ...patch,
      });
      if (result) {
        setLibraryNote(
          t("panel.batch.done_update", {
            updated: result.updated ?? 0,
            missing: (result.missing || []).length,
            defaultValue: "批量完成：改了 {updated}、不存在/失败 {missing}",
          }),
        );
      }
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const batchDelete = async () => {
    if (!selected.length) {
      return;
    }
    // 对齐参考系统的破坏性确认：先把精确张数摊开，再问要不要删。
    const answer = await confirm({
      title: t("panel.batch.delete_title", { defaultValue: "批量删除" }),
      message: t("panel.batch.delete_message", {
        count: selected.length,
        defaultValue: "将删掉 {count} 张图和它们的记录，不可恢复。",
      }),
      tone: "danger",
    });
    if (!answer) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(props, "batch_remove", {
        ids: selected,
      });
      if (result) {
        setLibraryNote(
          t("panel.batch.delete_done", {
            removed: result.removed ?? 0,
            missing: (result.missing || []).length,
            defaultValue: "已删 {removed} 张、不存在 {missing}",
          }),
        );
      }
      setSelected([]);
      await props.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const term = query.trim().toLowerCase();
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
  // 搜索语义：组名/组说明命中→整组都在；否则只留命中的图。
  // **轮 I 反转 v0.8.0 的“空区块不出现”**：零张的分类必须显示（否则建完就消失，
  // 等于没建）——但仅限“这个分类本来就没图”，搜索筛空的有图分类仍不出现。
  const sections: Section[] = [];
  groups.forEach((info) => {
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
  // 聚焦卡跟着最新库态走：被删/被筛掉就自动收起，不留幽灵卡。
  const focusRow = focus
    ? stickers.filter((row) => row.id === focus)[0] || null
    : null;

  return (
    <Page
      title={t("panel.title", { defaultValue: "表情包管理" })}
      subtitle={state.lanlan || ""}
    >
      <Stack gap={12}>
        {state.error_code ? (
          <Alert
            tone="danger"
            message={t(`panel.error.${state.error_code}`, {
              defaultValue: state.error_code,
            })}
          />
        ) : null}
        <Inline gap={16} align="center" wrap>
          <Switch
            checked={!!state.enabled}
            label={t("panel.switch", {
              defaultValue: "总开关（关闭后她不发图、看不到目录）",
            })}
            onChange={async (next: boolean) => {
              await callAction(props, "switch", { enabled: next });
              await props.api.refresh();
            }}
          />
          <Inline gap={6}>
            <StatusBadge
              tone="info"
              label={`${t("panel.stat.total", { defaultValue: "收藏" })} ${state.counts ? state.counts.total : 0}`}
            />
            <StatusBadge
              tone="success"
              label={`${t("panel.stat.sent", { defaultValue: "累计发出" })} ${state.counts ? state.counts.sent_total : 0}`}
            />
            <StatusBadge
              tone="warning"
              label={`${t("panel.stat.available", { defaultValue: "可用" })} ${state.counts ? state.counts.enabled : 0}`}
            />
          </Inline>
        </Inline>
        <Divider />
        <Stack gap={12}>
          <Card title={t("panel.card.usage", { defaultValue: "她最近用过的" })}>
            <ScrollArea height={320}>
              <DataTable
                data={state.usage || []}
                emptyText={t("panel.usage.empty", {
                  defaultValue: "还没有发送记录",
                })}
                columns={[
                  {
                    key: "at",
                    label: t("panel.usage.at", { defaultValue: "时刻" }),
                    render: (row: UsageRow) => formatTime(row.at),
                  },
                  {
                    key: "id",
                    label: t("panel.usage.id", { defaultValue: "表情" }),
                  },
                  {
                    key: "source",
                    label: t("panel.usage.source", { defaultValue: "来源" }),
                  },
                  {
                    key: "ok",
                    label: t("panel.usage.ok", { defaultValue: "结果" }),
                    render: (row: UsageRow) =>
                      row.ok ? (
                        <StatusBadge
                          tone="success"
                          label={t("panel.usage.sent", {
                            defaultValue: "已发出",
                          })}
                        />
                      ) : (
                        <StatusBadge
                          tone="danger"
                          label={t(`panel.error.${row.code}`, {
                            defaultValue: row.code || "failed",
                          })}
                        />
                      ),
                  },
                ]}
              />
            </ScrollArea>
            <Text>
              {t("panel.usage.note", {
                defaultValue: "台账只记时刻与表情 id，不含对话原文。",
              })}
            </Text>
          </Card>
        </Stack>
        <Card
          title={t("panel.awareness.title", { defaultValue: "存在感注入" })}
        >
          <Stack gap={8}>
            <Text>
              {t("panel.awareness.note", {
                defaultValue:
                  "低频把『你有一间表情收藏间 + 最近常用的几张』静默注进她的上下文：你看不到、她不会因此开口。",
              })}
            </Text>
            <Inline gap={16} align="center" wrap>
              <KeyValue
                items={[
                  {
                    key: "status",
                    label: t("panel.awareness.status", {
                      defaultValue: "最近一次",
                    }),
                    value: t(
                      `panel.awareness.status.${String((state.awareness && state.awareness.status) || "")}`,
                      {
                        defaultValue:
                          (state.awareness && state.awareness.status) || "—",
                      },
                    ),
                  },
                  {
                    key: "target",
                    label: t("panel.awareness.target", {
                      defaultValue: "注给",
                    }),
                    value: (state.awareness && state.awareness.target) || "—",
                  },
                  {
                    key: "next",
                    label: t("panel.awareness.next", {
                      defaultValue: "下次最快",
                    }),
                    value:
                      String(
                        Math.ceil(
                          (state.awareness &&
                            state.awareness.min_next_wait_sec) ||
                            0,
                        ),
                      ) + "s",
                  },
                ]}
              />
              <Button
                tone="default"
                onClick={() => {
                  ping();
                }}
              >
                {t("panel.awareness.button", {
                  defaultValue: "现在注一条（调试）",
                })}
              </Button>
            </Inline>
            {awarenessNote ? <Text>{awarenessNote}</Text> : null}
          </Stack>
        </Card>
        <Card title={t("panel.card.library", { defaultValue: "她的表情库" })}>
          <Stack gap={10}>
            <Inline gap={8} align="center" wrap>
              <Input
                value={query}
                onChange={setQuery}
                placeholder={t("panel.search", {
                  defaultValue: "按描述 / 标签 / id 过滤",
                })}
              />
              {/* 轮 I：分类是第一等对象——先建分类，后面的区块头才有地方收图。 */}
              <Button
                tone="primary"
                onClick={() => {
                  setCreating(!creating);
                }}
              >
                {t("panel.group.new_button", { defaultValue: "新建分类" })}
              </Button>
              <Button
                tone="primary"
                disabled={uploading}
                onClick={() => {
                  if (zipInputRef.current) {
                    zipInputRef.current.click();
                  }
                }}
              >
                {uploading
                  ? t("panel.upload.busy", { defaultValue: "上传中…" })
                  : t("panel.upload.pick", { defaultValue: "选择套图包导入" })}
              </Button>
              <input
                ref={zipInputRef}
                type="file"
                accept=".zip,application/zip"
                style={{ display: "none" }}
                onChange={(event: any) => {
                  const file =
                    event.target && event.target.files && event.target.files[0];
                  if (file) {
                    importZip(file);
                  }
                  event.target.value = "";
                }}
              />
              <Button
                tone="primary"
                onClick={() => {
                  importInbox();
                }}
              >
                {(t("panel.inbox.button", {
                  defaultValue: "导入收件箱",
                }) as string) +
                  (state.inbox && state.inbox.pending
                    ? " (" + state.inbox.pending + ")"
                    : "")}
              </Button>
              <Button
                tone="info"
                onClick={() => {
                  exportPack();
                }}
              >
                {t("panel.export.button", { defaultValue: "导出套图包" })}
              </Button>
              <Button
                tone="warning"
                onClick={() => {
                  repair();
                }}
              >
                {t("panel.repair.button", { defaultValue: "体检与修复" })}
              </Button>
            </Inline>
            {creating ? (
              <Stack gap={6}>
                <Field
                  label={t("panel.group.new_name", {
                    defaultValue: "分类名字（必填）",
                  })}
                  required
                >
                  <Input
                    value={newName}
                    onChange={setNewName}
                    placeholder={t("panel.group.new_name_ph", {
                      defaultValue: "例如：晚安与早安",
                    })}
                  />
                </Field>
                <Field
                  label={t("panel.group.new_desc", {
                    defaultValue:
                      "什么时候用这一组（可留空，之后在块头「编辑说明」补）",
                  })}
                >
                  <Input
                    value={newDesc}
                    onChange={setNewDesc}
                    placeholder={t("panel.group.new_desc_ph", {
                      defaultValue: "例如：她困了、要睡了、或在装睡",
                    })}
                  />
                </Field>
                <Inline gap={6}>
                  <Button
                    tone="primary"
                    onClick={() => {
                      createCategory();
                    }}
                  >
                    {t("panel.group.create_submit", {
                      defaultValue: "创建分类",
                    })}
                  </Button>
                  <Button
                    tone="default"
                    onClick={() => {
                      setCreating(false);
                      setNewName("");
                      setNewDesc("");
                    }}
                  >
                    {t("panel.group.create_cancel", { defaultValue: "取消" })}
                  </Button>
                </Inline>
              </Stack>
            ) : null}
            {/* 全库共用一个隐藏图片输入框：区块头的「收图」只改 collectTargetRef 再 click。 */}
            <input
              ref={imgInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              disabled={collectBusy}
              style={{ display: "none" }}
              onChange={(event: any) => {
                importFiles(
                  event.target && event.target.files,
                  collectTargetRef.current,
                );
                event.target.value = "";
              }}
            />
            {sections.length > 0 ? (
              // 轮 G：分类分区视图——每块「组名 · 张数 + 一句说明 + 就地操作」，
              // 一路滚下去就是她的收藏间目录。搜索时整块命中或逐图命中都支持。
              <Text>
                {t("panel.section.summary", {
                  groups: sections.length,
                  images: stickers.length,
                  defaultValue: "{groups} 个分区 · 共 {images} 张",
                })}
              </Text>
            ) : null}
            {selected.length > 0 ? (
              <Inline gap={6} align="center" wrap>
                <Text>
                  {t("panel.batch.selected", {
                    count: selected.length,
                    defaultValue: "已选 {count} 张",
                  })}
                </Text>
                <Input
                  value={batchTags}
                  onChange={setBatchTags}
                  placeholder={t("panel.batch.tags_ph", {
                    defaultValue: "标签，逗号分隔",
                  })}
                />
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ tags_add: batchTags });
                  }}
                >
                  {t("panel.batch.add_tags", { defaultValue: "加标签" })}
                </Button>
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ tags_remove: batchTags });
                  }}
                >
                  {t("panel.batch.remove_tags", { defaultValue: "删标签" })}
                </Button>
                <Text>
                  {t("panel.group.pick", { defaultValue: "选已有分类" })}
                </Text>
                <Select
                  value={batchGroup}
                  options={categoryOptions(props, t)}
                  onChange={(next: any) => {
                    setBatchGroup(
                      String(next === undefined || next === null ? "" : next),
                    );
                  }}
                />
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ group: batchGroup });
                  }}
                >
                  {t("panel.batch.move", { defaultValue: "移入分类" })}
                </Button>
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ disabled: false });
                  }}
                >
                  {t("panel.batch.enable", { defaultValue: "启用" })}
                </Button>
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ disabled: true });
                  }}
                >
                  {t("panel.batch.disable", { defaultValue: "禁用" })}
                </Button>
                <Button
                  tone="danger"
                  onClick={() => {
                    batchDelete();
                  }}
                >
                  {t("panel.batch.delete", { defaultValue: "删除所选" })}
                </Button>
                <Button
                  tone="default"
                  onClick={() => {
                    setSelected([]);
                  }}
                >
                  {t("panel.batch.clear", { defaultValue: "取消选择" })}
                </Button>
              </Inline>
            ) : null}
            {state.inbox && state.inbox.path ? (
              <Text>
                {t("panel.inbox.hint", {
                  path: state.inbox.path,
                  defaultValue:
                    "套图包也可以点「选择套图包导入」直接选文件；或把图片放进 {path} 后点「导入收件箱」，描述取自文件名。",
                })}
              </Text>
            ) : null}
            {libraryNote ? <Text>{libraryNote}</Text> : null}
            {focusRow ? (
              <FocusCard
                key={focusRow.id}
                surface={props}
                row={focusRow}
                onExit={() => {
                  setFocus("");
                }}
              />
            ) : null}
            {sections.length === 0 ? (
              <Stack gap={8}>
                <EmptyState
                  title={
                    term
                      ? t("panel.filter.empty_title", {
                          defaultValue: "当前筛选没有命中",
                        })
                      : groups.length
                        ? t("panel.empty.title", {
                            defaultValue: "库还是空的",
                          })
                        : t("panel.cat.empty_title", {
                            defaultValue: "还没有分类",
                          })
                  }
                  description={
                    term
                      ? t("panel.filter.empty_hint", {
                          defaultValue: "换个词试试，或清空搜索框。",
                        })
                      : groups.length
                        ? t("panel.empty.hint", {
                            defaultValue:
                              "点任意分类块头的「收图进这一类」，或直接用上面的套图包导入。",
                          })
                        : t("panel.cat.empty_hint", {
                            defaultValue:
                              "先建一个分类：名字必填，再补一句“什么时候用这一组”（那句话就是她选图时看到的分类正文）。有了分类，块头才有地方收图。",
                          })
                  }
                />
                {!term && !groups.length ? (
                  <Button
                    tone="primary"
                    onClick={() => {
                      setCreating(true);
                    }}
                  >
                    {t("panel.group.new_button", { defaultValue: "新建分类" })}
                  </Button>
                ) : null}
              </Stack>
            ) : (
              sections.map((section, index) => (
                <Stack key={section.key} gap={8}>
                  {index > 0 ? <Divider /> : null}
                  <Inline gap={8} align="center" wrap>
                    <Text>
                      {section.name}（{section.rows.length}）
                    </Text>
                    {/* 轮 I（2A）：收图长在分类上——选文件即收，不再问逐图字段。 */}
                    <Button
                      tone="primary"
                      disabled={collectBusy}
                      onClick={() => {
                        collectInto(section.group);
                      }}
                    >
                      {collectBusy
                        ? t("panel.collect.busy_one", {
                            defaultValue: "收藏中…",
                          })
                        : t("panel.collect.button", {
                            defaultValue: "收图进这一类",
                          })}
                    </Button>
                    {section.rows.length ? (
                      <Button
                        tone="default"
                        onClick={() => {
                          selectSection(section.rows);
                        }}
                      >
                        {t("panel.section.select_all", {
                          defaultValue: "全选本组",
                        })}
                      </Button>
                    ) : null}
                    {section.editable ? (
                      <Button
                        tone="default"
                        onClick={() => {
                          setDescEditing(
                            descEditing === section.group ? "" : section.group,
                          );
                          setDescDraft(section.desc);
                        }}
                      >
                        {t("panel.section.desc_edit", {
                          defaultValue: "编辑说明",
                        })}
                      </Button>
                    ) : null}
                    {section.editable ? (
                      <Button
                        tone="danger"
                        onClick={() => {
                          removeCategory(section);
                        }}
                      >
                        {t("panel.group.delete_button", {
                          defaultValue: "删分类",
                        })}
                      </Button>
                    ) : null}
                  </Inline>
                  {section.total === 0 ? (
                    <Text>
                      {t("panel.section.empty_hint", {
                        defaultValue:
                          "这个分类还没有表情：点上面「收图进这一类」选文件（可多选）；想逐图写梗义，收完点开图在聚焦卡里补。",
                      })}
                    </Text>
                  ) : null}
                  {section.editable && descEditing !== section.group ? (
                    <Text>
                      {section.desc ||
                        t("panel.section.desc_hint", {
                          defaultValue:
                            "未写说明——补一句『什么时候用这一组』，她目录里看到的分类正文就是它。",
                        })}
                    </Text>
                  ) : null}
                  {section.editable && descEditing === section.group ? (
                    <Inline gap={6} align="center" wrap>
                      <Input
                        value={descDraft}
                        onChange={setDescDraft}
                        placeholder={t("panel.group.desc_ph", {
                          defaultValue:
                            "什么时候用这一组——她选图时看到的分类正文",
                        })}
                      />
                      <Button
                        tone="primary"
                        onClick={() => {
                          saveGroupDesc();
                        }}
                      >
                        {t("panel.group.desc_save", {
                          defaultValue: "存分组说明",
                        })}
                      </Button>
                      <Button
                        tone="default"
                        onClick={() => {
                          setDescEditing("");
                        }}
                      >
                        {t("panel.section.desc_cancel", {
                          defaultValue: "取消",
                        })}
                      </Button>
                    </Inline>
                  ) : null}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "repeat(auto-fill, minmax(128px, 176px))",
                      gap: 8,
                    }}
                  >
                    {section.rows.map((row) => (
                      <StickerTile
                        key={row.id}
                        row={row}
                        surface={props}
                        selected={selected.indexOf(row.id) >= 0}
                        onToggleSelect={() => {
                          toggleSelected(row.id);
                        }}
                        onOpen={() => {
                          setFocus(focus === row.id ? "" : row.id);
                        }}
                      />
                    ))}
                  </div>
                </Stack>
              ))
            )}
          </Stack>
        </Card>
      </Stack>
    </Page>
  );
}
