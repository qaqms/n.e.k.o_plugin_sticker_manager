// Hosted TSX 面板：表情包管理器。只从 `@neko/plugin-ui` 导入，业务逻辑全在 Python 侧。
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
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type StickerRow = {
  id: string
  file?: string
  desc?: string
  tags?: string[]
  disabled?: boolean
  added_at?: number
  use_count?: number
  last_used_at?: number
}

type UsageRow = {
  at?: number
  id?: string
  lanlan?: string
  source?: string
  ok?: boolean
  code?: string
}

type State = {
  enabled?: boolean
  lanlan?: string
  counts?: { total?: number; enabled?: number; sent_total?: number }
  stickers?: StickerRow[]
  usage?: UsageRow[]
  config?: { cooldown_sec?: number; inline_max_bytes?: number; catalog_limit_for_model?: number }
  error_code?: string
}

type Surface = PluginSurfaceProps<State>

// 预览缓存：id -> dataUrl。失败记空串，避免每张坏图都重试一轮。
const previewCache: Record<string, string> = {}

function formatTime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return "—"
  return new Date(seconds * 1000).toLocaleString()
}

function dataUrlToBase64(value: string): string {
  const comma = value.indexOf(",")
  return comma >= 0 ? value.slice(comma + 1) : value
}

async function callAction(surface: Surface, actionId: string, args: Record<string, any>): Promise<any> {
  const envelope = await surface.api.call(actionId, args)
  return envelope ? envelope.result : null
}

function StickerCard(props: { key?: string; row: StickerRow; surface: Surface }) {
  const row = props.row
  const surface = props.surface
  const t = surface.t
  const confirm = useConfirm()
  const [preview, setPreview] = useState<string>(previewCache[row.id] || "")
  const [editing, setEditing] = useState<boolean>(false)
  const [editDesc, setEditDesc] = useState<string>(row.desc || "")
  const [editTags, setEditTags] = useState<string>((row.tags || []).join(","))

  const loadPreview = async () => {
    if (previewCache[row.id] !== undefined) {
      setPreview(previewCache[row.id])
      return
    }
    let dataUrl = ""
    try {
      const result = await callAction(surface, "preview", { id: row.id })
      dataUrl = String((result && result.data_url) || "")
    } catch (error) {
      dataUrl = ""
    }
    previewCache[row.id] = dataUrl
    setPreview(dataUrl)
  }

  const run = async (actionId: string, args: Record<string, any>) => {
    try {
      await callAction(surface, actionId, args)
      await surface.api.refresh()
    } catch (error) {
      // 错误码是稳定 ASCII（契约见 DESIGN.md）；toast 直出码，面板不猜文案。
      console.warn("sticker_manager action failed", actionId, error)
    }
  }

  const remove = async () => {
    const answer = await confirm({
      title: t("panel.remove.title", { defaultValue: "删除表情包" }),
      message: t("panel.remove.message", { defaultValue: "这张图和它的记录都会被删掉，不可恢复。" }),
      tone: "danger",
    })
    if (answer) {
      await run("remove", { id: row.id })
    }
  }

  return (
    <Card title={row.desc || row.id}>
      <Stack gap={8}>
        <Inline gap={8} align="start">
          <div style={{ width: 120 }}>
            {preview ? (
              <ImagePreview src={preview} alt={row.desc || row.id} />
            ) : (
              <Button tone="default" onClick={() => { loadPreview() }}>
                {t("panel.thumb.load", { defaultValue: "加载预览" })}
              </Button>
            )}
          </div>
          <Stack gap={4}>
            <KeyValue
              items={[
                { key: "id", label: "id", value: row.id },
                { key: "uses", label: t("panel.thumb.uses", { defaultValue: "发出次数" }), value: String(row.use_count || 0) },
                { key: "last", label: t("panel.thumb.last", { defaultValue: "最近发出" }), value: formatTime(row.last_used_at) },
              ]}
            />
            <Inline gap={6} wrap>
              {[
                ...(row.disabled ? [<StatusBadge tone="warning" label={t("panel.badge.disabled", { defaultValue: "已禁用" })} />] : []),
                ...(row.tags || []).map((tag) => <StatusBadge tone="info" label={tag} />),
              ]}
            </Inline>
          </Stack>
        </Inline>
        <Inline gap={6} wrap>
          <Button tone="success" onClick={() => { run("send", { id: row.id }) }}>
            {t("panel.action.send", { defaultValue: "发到聊天" })}
          </Button>
          <Button tone="default" onClick={() => { setEditing(true) }}>
            {t("panel.action.edit", { defaultValue: "编辑" })}
          </Button>
          <Button tone="warning" onClick={() => { run("update", { id: row.id, disabled: !row.disabled }) }}>
            {row.disabled
              ? t("panel.action.enable", { defaultValue: "恢复启用" })
              : t("panel.action.disable", { defaultValue: "禁用" })}
          </Button>
          <Button tone="danger" onClick={() => { remove() }}>
            {t("panel.action.remove", { defaultValue: "删除" })}
          </Button>
        </Inline>
      </Stack>
      <Modal open={editing} title={t("panel.edit.title", { defaultValue: "编辑这条表情包" })} onClose={() => { setEditing(false) }}>
        <Stack gap={8}>
          <Field label={t("panel.edit.desc", { defaultValue: "描述（她选图的唯一依据）" })}>
            <Input value={editDesc} onChange={setEditDesc} placeholder={t("panel.edit.desc.ph", { defaultValue: "一句话说明图里在干什么" })} />
          </Field>
          <Field label={t("panel.edit.tags", { defaultValue: "标签（逗号分隔）" })}>
            <Input value={editTags} onChange={setEditTags} placeholder="开心, 猫" />
          </Field>
          <Inline gap={6}>
            <Button
              tone="primary"
              onClick={() => {
                setEditing(false)
                run("update", { id: row.id, desc: editDesc, tags: editTags })
              }}
            >
              {t("panel.edit.save", { defaultValue: "保存" })}
            </Button>
            <Button tone="default" onClick={() => { setEditing(false) }}>
              {t("panel.edit.cancel", { defaultValue: "取消" })}
            </Button>
          </Inline>
        </Stack>
      </Modal>
    </Card>
  )
}

function AddForm(props: { surface: Surface }) {
  const surface = props.surface
  const t = surface.t
  const [artifact, setArtifact] = useState<any>(null)
  const [desc, setDesc] = useState("")
  const [tags, setTags] = useState("")
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const dataUrl = String((artifact && artifact.dataUrl) || "")
    if (!dataUrl) {
      setBusy(false)
      return
    }
    if (!desc.trim()) {
      setBusy(false)
      return
    }
    setBusy(true)
    try {
      await callAction(surface, "add", {
        data_base64: dataUrlToBase64(dataUrl),
        desc: desc.trim(),
        tags: tags,
      })
      setArtifact(null)
      setDesc("")
      setTags("")
      await surface.api.refresh()
    } catch (error) {
      console.warn("sticker_manager add failed", error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack gap={8}>
      <Field label={t("panel.add.image", { defaultValue: "图片（png / jpg / gif / webp，≤8MiB）" })} required>
        <ImageUpload
          value={artifact}
          accept="image/png,image/jpeg,image/gif,image/webp"
          maxBytes={8 * 1024 * 1024}
          label={t("panel.add.pick", { defaultValue: "选择图片" })}
          onChange={setArtifact}
        />
      </Field>
      <Field label={t("panel.edit.desc", { defaultValue: "描述（她选图的唯一依据）" })} required>
        <Input value={desc} onChange={setDesc} placeholder={t("panel.add.desc.ph", { defaultValue: "例如：猫咪开心挥手" })} />
      </Field>
      <Field label={t("panel.edit.tags", { defaultValue: "标签（逗号分隔）" })}>
        <Input value={tags} onChange={setTags} placeholder="开心, 猫" />
      </Field>
      <Button tone="primary" disabled={busy} onClick={() => { submit() }}>
        {busy
          ? t("panel.add.busy", { defaultValue: "收藏中…" })
          : t("panel.add.submit", { defaultValue: "收进表情库" })}
      </Button>
    </Stack>
  )
}

export default function Panel(props: Surface) {
  const state = props.state || {}
  const t = props.t
  const [query, setQuery] = useState("")
  const stickers = state.stickers || []
  const term = query.trim().toLowerCase()
  const rows = term
    ? stickers.filter((row) => {
        const haystack = [row.id, row.desc || "", (row.tags || []).join(" ")].join(" ").toLowerCase()
        return haystack.indexOf(term) >= 0
      })
    : stickers

  return (
    <Page title={t("panel.title", { defaultValue: "表情包管理器" })} subtitle={state.lanlan || ""}>
      <Stack gap={12}>
        {state.error_code ? (
          <Alert tone="danger" message={t(`panel.error.${state.error_code}`, { defaultValue: state.error_code })} />
        ) : null}
        <Inline gap={16} align="center" wrap>
          <Switch
            checked={!!state.enabled}
            label={t("panel.switch", { defaultValue: "总开关（关闭后她不发图、看不到目录）" })}
            onChange={async (next: boolean) => {
              await callAction(props, "switch", { enabled: next })
              await props.api.refresh()
            }}
          />
          <Inline gap={6}>
            <StatusBadge tone="info" label={`${t("panel.stat.total", { defaultValue: "收藏" })} ${state.counts ? state.counts.total : 0}`} />
            <StatusBadge tone="success" label={`${t("panel.stat.sent", { defaultValue: "累计发出" })} ${state.counts ? state.counts.sent_total : 0}`} />
            <StatusBadge tone="warning" label={`${t("panel.stat.available", { defaultValue: "可用" })} ${state.counts ? state.counts.enabled : 0}`} />
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
                emptyText={t("panel.usage.empty", { defaultValue: "还没有发送记录" })}
                columns={[
                  { key: "at", label: t("panel.usage.at", { defaultValue: "时刻" }), render: (row: UsageRow) => formatTime(row.at) },
                  { key: "id", label: t("panel.usage.id", { defaultValue: "表情" }) },
                  { key: "source", label: t("panel.usage.source", { defaultValue: "来源" }) },
                  {
                    key: "ok",
                    label: t("panel.usage.ok", { defaultValue: "结果" }),
                    render: (row: UsageRow) =>
                      row.ok
                        ? <StatusBadge tone="success" label={t("panel.usage.sent", { defaultValue: "已发出" })} />
                        : <StatusBadge tone="danger" label={row.code || "failed"} />,
                  },
                ]}
              />
            </ScrollArea>
            <Text>{t("panel.usage.note", { defaultValue: "台账只记时刻与表情 id，不含对话原文。" })}</Text>
          </Card>
        </Grid>
        <Card title={t("panel.card.library", { defaultValue: "她的表情库" })}>
          <Stack gap={10}>
            <Input value={query} onChange={setQuery} placeholder={t("panel.search", { defaultValue: "按描述 / 标签 / id 过滤" })} />
            {rows.length === 0 ? (
              <EmptyState
                title={t("panel.empty.title", { defaultValue: "库还是空的" })}
                description={t("panel.empty.hint", { defaultValue: "在上方收藏第一张表情，她就能在对话里把它甩出去。" })}
              />
            ) : (
              <Grid cols={2} gap={10}>
                {rows.map((row) => (
                  <StickerCard key={row.id} row={row} surface={props} />
                ))}
              </Grid>
            )}
          </Stack>
        </Card>
      </Stack>
    </Page>
  )
}
