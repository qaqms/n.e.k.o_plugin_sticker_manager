// 聚焦卡（v0.9.1，v0.10.1 拆分自 panel.tsx）：点格子后在墙上方就地展开。
// 为什么不用弹窗——kit 的 .neko-page/.neko-card 都挂着 animation ... both，
// 关键帧终帧 transform:translateY(0) 被永久保留，任何 position:fixed 后代都被
// 收进祖先包含块、再被 Card 的 overflow:hidden 裁切（实机截图钉的：遮罩只压暗
// 卡片区域、弹窗底部直接裁没，见 DESIGN.md 陷阱 20）。插件侧修不动平台 CSS，
// 所以这里零 overlay、零 fixed：大图（不放大、原尺寸封顶）+ 全属性 + 动作排，
// 「编辑」在同一张卡里就地切表单——看做分离的语义不变，载体换硬了。

import {
  Alert,
  Button,
  Card,
  Field,
  Inline,
  Input,
  KeyValue,
  Select,
  Stack,
  Text,
  useConfirm,
  useState,
} from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import { callAction, categoryOptions, extractCode, formatTime } from "../shared";
import type { StickerRow, Surface } from "../shared";

export function FocusCard(props: {
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
                options={categoryOptions(surface, t, String(row.zone || ""))}
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
