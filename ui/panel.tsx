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
  Checkbox,
  DataTable,
  Divider,
  EmptyState,
  Field,
  Grid,
  ImagePreview,
  ImageUpload,
  Input,
  Inline,
  KeyValue,
  Modal,
  Page,
  ScrollArea,
  Stack,
  StatusBadge,
  Switch,
  Text,
  useConfirm,
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

// 轮 G：浏览主形态——一个分组一个区块（组名 + 张数 + 组说明 + 块内网格）。
type Section = {
  key: string;
  name: string;
  desc: string;
  rows: StickerRow[];
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

// 后端 Err 抛出的 message 里抓稳定 ASCII 码（^[a-z][a-z0-9_]*$，DESIGN.md 错误码契约）；
// 抓不到就原样直出（宿主/网络错误的原文比编一个码诚实）。
function extractCode(raw: string): string {
  const m = raw.match(/[a-z][a-z0-9_]*/);
  return m ? m[0] : raw;
}

function artifactFileName(artifact: any): string {
  const name = String((artifact && (artifact.filename || artifact.name)) || "");
  const base = name.split(/[\\/]/).pop() || "";
  return base
    .replace(/\.[A-Za-z0-9]+$/, "")
    .replace(/[_-]+/g, " ")
    .trim();
}

// 批量通道单次上限：防一次拖几百张把插件子进程堆满 base64；超出部分如实报数。
const MAX_BATCH_FILES = 64;
// 与 core.catalog.MAX_STICKER_BYTES 同数（iframe 碰不到 Python，跨运行时重复）。
const MAX_STICKER_BYTES = 8 * 1024 * 1024;

// 与 core.catalog.desc_from_filename 同步修改（跨运行时的等价小函数）。
function guessDesc(name: string): string {
  const base =
    String(name || "")
      .split(/[\\/]/)
      .pop() || "";
  const stem = base.replace(/\.[^.]+$/, "");
  const cleaned = stem
    .replace(/[_\-+.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (cleaned || "sticker").slice(0, 200);
}

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

function StickerCard(props: {
  key?: string;
  row: StickerRow;
  surface: Surface;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  const row = props.row;
  const surface = props.surface;
  const t = surface.t;
  const confirm = useConfirm();
  const [preview, setPreview] = useState<string>(previewCache[row.id] || "");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<boolean>(false);
  const [editDesc, setEditDesc] = useState<string>(row.desc || "");
  const [editTags, setEditTags] = useState<string>((row.tags || []).join(","));
  const [editGroup, setEditGroup] = useState<string>(row.group || "");
  const [editCaption, setEditCaption] = useState<string>(row.caption || "");
  const [editVisible, setEditVisible] = useState<string>(
    row.visible_text || "",
  );

  const loadPreview = async () => {
    if (previewCache[row.id] !== undefined) {
      setPreview(previewCache[row.id]);
      return;
    }
    let dataUrl = "";
    try {
      // 分段拉取拼回 dataUrl：宿主 entry 回包单帧上限≈4.56MiB，整张大图会被
      // 传输层拒发（实机超时钉的坑）。循环有护栏，坏协议不许无限转。
      let offset = 0;
      let mime = "";
      const parts: string[] = [];
      for (let guard = 0; guard < 16; guard += 1) {
        const result = await callAction(surface, "preview", {
          id: row.id,
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
    previewCache[row.id] = dataUrl;
    setPreview(dataUrl);
  };

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
    }
  };

  return (
    <Card title={row.caption || row.desc || row.id}>
      <Stack gap={8}>
        <Inline gap={8} align="start">
          {props.onToggleSelect ? (
            <Checkbox
              checked={!!props.selected}
              onChange={() => {
                if (props.onToggleSelect) {
                  props.onToggleSelect();
                }
              }}
              label={t("panel.batch.pick", { defaultValue: "选" })}
            />
          ) : null}
          <div style={{ width: 120 }}>
            {preview ? (
              <ImagePreview src={preview} alt={row.desc || row.id} />
            ) : (
              <Button
                tone="default"
                onClick={() => {
                  loadPreview();
                }}
              >
                {t("panel.thumb.load", { defaultValue: "加载预览" })}
              </Button>
            )}
          </div>
          <Stack gap={4}>
            <KeyValue
              items={[
                { key: "id", label: "id", value: row.id },
                {
                  key: "uses",
                  label: t("panel.thumb.uses", { defaultValue: "发出次数" }),
                  value: String(row.use_count || 0),
                },
                {
                  key: "last",
                  label: t("panel.thumb.last", { defaultValue: "最近发出" }),
                  value: formatTime(row.last_used_at),
                },
              ]}
            />
            <Inline gap={6} wrap>
              {[
                ...(row.disabled
                  ? [
                      <StatusBadge
                        tone="warning"
                        label={t("panel.badge.disabled", {
                          defaultValue: "已禁用",
                        })}
                      />,
                    ]
                  : []),
                ...(row.group
                  ? [<StatusBadge tone="info" label={row.group} />]
                  : []),
                ...(row.tags || []).map((tag) => (
                  <StatusBadge tone="info" label={tag} />
                )),
              ]}
            </Inline>
            {row.caption ? <Text>{row.caption}</Text> : null}
          </Stack>
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
        </Inline>
        {note ? <Alert tone="danger" message={note} /> : null}
      </Stack>
      <Modal
        open={editing}
        title={t("panel.edit.title", { defaultValue: "编辑这条表情包" })}
        onClose={() => {
          setEditing(false);
        }}
      >
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
              defaultValue: "套图分组（留空=移出分组）",
            })}
          >
            <Input
              value={editGroup}
              onChange={setEditGroup}
              placeholder={t("panel.group.label", { defaultValue: "套图分组" })}
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
      </Modal>
    </Card>
  );
}

function AddForm(props: { surface: Surface }) {
  const surface = props.surface;
  const t = surface.t;
  const [artifact, setArtifact] = useState<any>(null);
  const [desc, setDesc] = useState("");
  const [caption, setCaption] = useState("");
  const [tags, setTags] = useState("");
  const [group, setGroup] = useState("");
  const [busy, setBusy] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState("");
  // 反馈：{kind: ""|"ok"|"err", text}。重复入库（duplicate_image）等失败必须让用户看见，
  // 只 console.warn 等于静默吞掉。
  const [feedback, setFeedback] = useState<{ kind: string; text: string }>({
    kind: "",
    text: "",
  });
  // 用户亲手改过描述后，不再用文件名覆盖它。
  const [descTouched, setDescTouched] = useState(false);

  const pick = (next: any) => {
    setArtifact(next);
    if (!descTouched) {
      const guess = artifactFileName(next);
      if (guess) {
        setDesc(guess);
      }
    }
  };

  // 批量多选：逐张走已测的 add 通道（魔数/查重/体积限制全复用），
  // 描述取自文件名，收完在库里逐个编辑。失败按四类计数，不拼长报告。
  const importMany = async (files: any) => {
    const list: any[] = Array.from(files || []);
    if (!list.length || batchBusy) {
      return;
    }
    const taken = list.slice(0, MAX_BATCH_FILES);
    setBatchBusy(true);
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
            const result = await callAction(surface, "add", {
              data_base64: b64,
              desc: guessDesc(String(file.name || "")),
              tags: tags,
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
      setBatchNote(
        t("panel.batch.busy", {
          done: i + 1,
          total: taken.length,
          defaultValue: "收藏中 {done}/{total}…",
        }),
      );
    }
    setBatchBusy(false);
    await surface.api.refresh();
    const extra = list.length - taken.length;
    setBatchNote(
      t("panel.batch.done", {
        ok: ok,
        dup: dup,
        big: big,
        fail: fail,
        defaultValue:
          "已收 {ok} · 重复跳过 {dup} · 超限略过 {big} · 失败 {fail}",
      }) +
        (extra > 0
          ? t("panel.batch.more", {
              extra: extra,
              defaultValue: "；本次未处理 {extra} 张",
            })
          : ""),
    );
  };

  const submit = async () => {
    const dataUrl = String((artifact && artifact.dataUrl) || "");
    if (!dataUrl) {
      setFeedback({
        kind: "err",
        text: t("panel.add.need_image", { defaultValue: "先选一张图" }),
      });
      return;
    }
    // 轮 F：逐图描述不再必填（参考系统逐图零文本也能用）——不写也能收，
    // 她靠分组说明/梗义选图；这里只拦图。
    setBusy(true);
    setFeedback({ kind: "", text: "" });
    try {
      const result = await callAction(surface, "add", {
        data_base64: dataUrlToBase64(dataUrl),
        desc: desc.trim(),
        caption: caption.trim(),
        tags: tags,
        group: group,
      });
      if (result && result.note === "sticker_added") {
        setFeedback({
          kind: "ok",
          text: t("panel.add.ok", { defaultValue: "已收进她的表情库" }),
        });
        setArtifact(null);
        setDesc("");
        setCaption("");
        setTags("");
        setGroup("");
        setDescTouched(false);
        await surface.api.refresh();
      }
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setFeedback({
        kind: "err",
        text: t("panel.add.fail", {
          code: extractCode(raw),
          defaultValue: "收藏失败：{code}",
        }),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack gap={8}>
      {feedback.text ? (
        <Alert
          tone={feedback.kind === "ok" ? "success" : "danger"}
          message={feedback.text}
        />
      ) : null}
      <Field
        label={t("panel.add.image", {
          defaultValue: "图片（png / jpg / gif / webp，≤8MiB）",
        })}
        required
      >
        <ImageUpload
          value={artifact}
          accept="image/png,image/jpeg,image/gif,image/webp"
          maxBytes={8 * 1024 * 1024}
          label={t("panel.add.pick", { defaultValue: "选择图片" })}
          onChange={pick}
        />
      </Field>
      <Field
        label={t("panel.add.desc_optional", {
          defaultValue: "描述（可选：不写也行，她靠分组说明/梗义选图）",
        })}
      >
        <Input
          value={desc}
          onChange={(next: string) => {
            setDescTouched(true);
            setDesc(next);
          }}
          placeholder={t("panel.add.desc.ph", {
            defaultValue: "例如：猫咪开心挥手",
          })}
        />
      </Field>
      <Field
        label={t("panel.edit.caption", {
          defaultValue: "梗义（她选图时看到的正文；留空=清掉标注）",
        })}
      >
        <Input
          value={caption}
          onChange={setCaption}
          placeholder={t("panel.edit.caption.ph", {
            defaultValue: "例：被催了很久之后终于交差，得意中带点解脱",
          })}
        />
      </Field>
      <Field label={t("panel.edit.tags", { defaultValue: "标签（逗号分隔）" })}>
        <Input value={tags} onChange={setTags} placeholder="开心, 猫" />
      </Field>
      <Field label={t("panel.group.label", { defaultValue: "套图分组" })}>
        <Input
          value={group}
          onChange={setGroup}
          placeholder={t("panel.group.ph", {
            defaultValue: "可选，如：猫猫日常",
          })}
        />
      </Field>
      <Button
        tone="primary"
        disabled={busy}
        onClick={() => {
          submit();
        }}
      >
        {busy
          ? t("panel.add.busy", { defaultValue: "收藏中…" })
          : t("panel.add.submit", { defaultValue: "收进表情库" })}
      </Button>
      <Divider />
      <Field
        label={t("panel.batch.label", {
          defaultValue: "批量收藏：多选文件，文件名当描述，收完可逐个编辑",
        })}
      >
        <input
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          multiple
          disabled={batchBusy}
          onChange={(event: any) => {
            importMany(event.target.files);
            event.target.value = "";
          }}
        />
      </Field>
      {batchNote ? <Text>{batchNote}</Text> : null}
    </Stack>
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
  // 轮 G：分组说明改为就地编辑——同一时刻只开一个区块的编辑行。
  const [descEditing, setDescEditing] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const confirm = useConfirm();
  const zipInputRef = useRef<any>(null);
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
  // 搜索语义对齐参考面板：组名/组说明命中→整组都在；否则只留命中的图；空区块不出现。
  const sections: Section[] = [];
  groups.forEach((info) => {
    const catHit =
      !term ||
      `${info.name} ${info.desc || ""}`.toLowerCase().indexOf(term) >= 0;
    const rows = stickers.filter(
      (row) => row.group === info.name && (catHit || rowMatches(row)),
    );
    if (rows.length) {
      sections.push({
        key: info.name,
        name: info.name,
        desc: info.desc || "",
        rows,
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
      editable: false,
    });
  }

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
        <Grid cols={2} gap={12}>
          <Card title={t("panel.card.add", { defaultValue: "收一张新表情" })}>
            <AddForm surface={props} />
          </Card>
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
        </Grid>
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
                <Input
                  value={batchGroup}
                  onChange={setBatchGroup}
                  placeholder={t("panel.batch.group_ph", {
                    defaultValue: "移入的分组名",
                  })}
                />
                <Button
                  tone="default"
                  onClick={() => {
                    runBatch({ group: batchGroup });
                  }}
                >
                  {t("panel.batch.move", { defaultValue: "移组" })}
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
            {sections.length === 0 ? (
              <EmptyState
                title={
                  term
                    ? t("panel.filter.empty_title", {
                        defaultValue: "当前筛选没有命中",
                      })
                    : t("panel.empty.title", {
                        defaultValue: "库还是空的",
                      })
                }
                description={
                  term
                    ? t("panel.filter.empty_hint", {
                        defaultValue: "换个词试试，或清空搜索框。",
                      })
                    : t("panel.empty.hint", {
                        defaultValue:
                          "在上方收藏第一张表情，她就能在对话里把它甩出去。",
                      })
                }
              />
            ) : (
              sections.map((section, index) => (
                <Stack key={section.key} gap={8}>
                  {index > 0 ? <Divider /> : null}
                  <Inline gap={8} align="center" wrap>
                    <Text>
                      {section.name}（{section.rows.length}）
                    </Text>
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
                    {section.editable ? (
                      <Button
                        tone="default"
                        onClick={() => {
                          setDescEditing(
                            descEditing === section.key ? "" : section.key,
                          );
                          setDescDraft(section.desc);
                        }}
                      >
                        {t("panel.section.desc_edit", {
                          defaultValue: "编辑说明",
                        })}
                      </Button>
                    ) : null}
                  </Inline>
                  {section.editable && descEditing !== section.key ? (
                    <Text>
                      {section.desc ||
                        t("panel.section.desc_hint", {
                          defaultValue:
                            "未写说明——补一句『什么时候用这一组』，她目录里看到的分类正文就是它。",
                        })}
                    </Text>
                  ) : null}
                  {section.editable && descEditing === section.key ? (
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
                  <Grid cols={2} gap={10}>
                    {section.rows.map((row) => (
                      <StickerCard
                        key={row.id}
                        row={row}
                        surface={props}
                        selected={selected.indexOf(row.id) >= 0}
                        onToggleSelect={() => {
                          toggleSelected(row.id);
                        }}
                      />
                    ))}
                  </Grid>
                </Stack>
              ))
            )}
          </Stack>
        </Card>
      </Stack>
    </Page>
  );
}
