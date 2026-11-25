// 区标题行（第 2 阶段重排）：图库右侧顶上一行说清“这是哪个区、猫娘在不在用它”。
// - 正在用的区显示“猫娘正在用”；别的区直接给“让猫娘用这个区”按钮；
// - 改名、改说明、新建、恢复官方收藏、删除收进“⋯”菜单（就地展开，零覆盖层，陷阱 20）；
// - 删除区保留 3 秒确认（纯前端闸），确认里摊服务端张数（zone.total，陷阱 22 的对偶）；
// - 窄屏时左栏的收藏区一节隐藏，这里多出一个区下拉（CSS 控制显隐）。
import { Button, Inline, Input, Select, Text } from "@neko/plugin-ui";
import { useEffect, useState } from "@neko/plugin-ui";
import type { Surface, ZoneInfo } from "../shared";

export function ZoneBar(props: {
  key?: string;
  surface: Surface;
  zones: ZoneInfo[];
  view: string;
  activeZone: string;
  categories?: number;
  showRestore?: boolean;
  pending?: string;
  onSwitch?: (zoneId: string) => void;
  onCreate?: (name: string, desc: string) => Promise<boolean>;
  onRename?: (zoneId: string, name: string) => Promise<boolean>;
  onSetDesc?: (zoneId: string, desc: string) => Promise<boolean>;
  onActivate?: (zoneId: string) => Promise<boolean>;
  onRemove?: (zoneId: string, name: string) => Promise<boolean>;
  onRestore?: () => Promise<boolean>;
}) {
  const t = props.surface.t;
  const busy = !!props.pending;
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [editing, setEditing] = useState(""); // "" | "rename" | "desc"
  const [editDraft, setEditDraft] = useState("");
  const [armed, setArmed] = useState(false);
  const [armLeft, setArmLeft] = useState(0);
  const current = props.zones.filter((zone) => zone.id === props.view)[0] || null;
  const live = !!current && current.id === props.activeZone;

  // 3 秒倒计时：armed 期间每秒减一；拆完/取消/换区都归零。
  useEffect(() => {
    if (!armed || armLeft <= 0) return;
    const timer = setInterval(() => setArmLeft((previous) => previous - 1), 1000);
    return () => clearInterval(timer);
  }, [armed, armLeft]);
  useEffect(() => {
    setArmed(false);
    setArmLeft(0);
    setEditing("");
  }, [props.view, props.zones.length]);

  const submitCreate = async () => {
    const name = String(nameDraft || "").trim();
    if (busy || !name || !props.onCreate) return;
    if (await props.onCreate(name, String(descDraft || "").trim())) {
      setNameDraft("");
      setDescDraft("");
      setCreating(false);
    }
  };
  const submitEdit = async () => {
    if (!current || busy) return;
    const value = String(editDraft || "").trim();
    if (editing === "rename") {
      if (value && props.onRename && await props.onRename(current.id, value)) setEditing("");
      return;
    }
    if (editing === "desc" && props.onSetDesc && await props.onSetDesc(current.id, value)) setEditing("");
  };
  const optionLabel = (zone: ZoneInfo) => `${zone.id === props.activeZone ? "● " : ""}${zone.name} · ${zone.total ?? 0}`;

  return (
    <div className="sticker-zonehead-wrap">
      <div className="sticker-zonehead">
        <label className="sticker-zone-select">
          <span className="sticker-sr-only">{t("panel.zone.browse", { defaultValue: "收藏区" })}</span>
          <Select
            value={props.view}
            disabled={busy}
            options={props.zones.map((zone) => ({ value: zone.id, label: optionLabel(zone) }))}
            onChange={(next: any) => props.onSwitch?.(String(next ?? ""))}
          />
        </label>
        <h2 className="sticker-zonehead-name">{current ? current.name : "—"}</h2>
        {current ? (
          <span className="sticker-zonehead-sub">
            {t("panel.zone.head_sub", {
              count: current.total ?? 0,
              categories: props.categories ?? 0,
              defaultValue: "{count} 张 · {categories} 个分类",
            })}
            {current.builtin ? t("panel.zone.builtin_sub", { defaultValue: " · 随插件自带" }) : ""}
          </span>
        ) : null}
        {live ? (
          <span className="sticker-zone-live">{t("panel.zone.in_use", { defaultValue: "猫娘正在用" })}</span>
        ) : current ? (
          <Button tone="primary" disabled={busy} onClick={() => { props.onActivate?.(current.id); }}>
            {t("panel.zone.activate", { defaultValue: "让猫娘用这个区" })}
          </Button>
        ) : null}
        <details className="sticker-menu sticker-zone-more">
          <summary
            aria-label={t("panel.zone.more", { defaultValue: "更多区操作" })}
            aria-disabled={busy}
            onClick={(event: any) => { if (busy) event.preventDefault(); }}
          >
            {t("panel.zone.more_short", { defaultValue: "⋯" })}
          </summary>
          <div className="sticker-menu-body">
            <Inline gap={6} align="center" wrap>
              {current ? (
                <Button
                  disabled={busy}
                  onClick={() => {
                    setEditing(editing === "rename" ? "" : "rename");
                    setEditDraft(current.name || "");
                  }}
                >
                  {t("panel.zone.rename", { defaultValue: "改名" })}
                </Button>
              ) : null}
              {current ? (
                <Button
                  disabled={busy}
                  onClick={() => {
                    setEditing(editing === "desc" ? "" : "desc");
                    setEditDraft(current.desc || "");
                  }}
                >
                  {t("panel.zone.edit_desc", { defaultValue: "改说明" })}
                </Button>
              ) : null}
              <Button
                disabled={busy}
                onClick={() => {
                  setCreating(!creating);
                  setNameDraft("");
                  setDescDraft("");
                }}
              >
                {t("panel.zone.new", { defaultValue: "新建收藏区" })}
              </Button>
              {/* J-2 拍板 P3：官方区不在册且随包官方装在——给一个补救入口（长任务，model 层已配 LONG_CALL）。 */}
              {props.showRestore ? (
                <Button disabled={busy} onClick={() => { props.onRestore?.(); }}>
                  {t("panel.zone.restore_official", { defaultValue: "恢复官方收藏" })}
                </Button>
              ) : null}
              {current && props.zones.length > 1 ? (
                <Button
                  tone="danger"
                  disabled={busy}
                  onClick={() => {
                    setArmed(!armed);
                    setArmLeft(3);
                  }}
                >
                  {t("panel.zone.remove", { defaultValue: "删除这个区" })}
                </Button>
              ) : null}
            </Inline>
            {creating ? (
              <Inline gap={6} align="center" wrap>
                <Input
                  value={nameDraft}
                  disabled={busy}
                  onChange={setNameDraft}
                  placeholder={t("panel.zone.name_ph_short", { defaultValue: "区名字，如：节日限定" })}
                />
                <Input
                  value={descDraft}
                  disabled={busy}
                  onChange={setDescDraft}
                  placeholder={t("panel.zone.desc_ph_short", { defaultValue: "一句说明（可选）" })}
                />
                <Button tone="primary" disabled={busy || !String(nameDraft || "").trim()} onClick={() => { submitCreate(); }}>
                  {t("panel.zone.create_short", { defaultValue: "新建" })}
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => {
                    setCreating(false);
                    setNameDraft("");
                    setDescDraft("");
                  }}
                >
                  {t("panel.zone.cancel", { defaultValue: "取消" })}
                </Button>
              </Inline>
            ) : null}
            {editing ? (
              <Inline gap={6} align="center" wrap>
                <Input
                  value={editDraft}
                  disabled={busy}
                  onChange={setEditDraft}
                  placeholder={editing === "rename"
                    ? t("panel.zone.rename_ph", { defaultValue: "新区名" })
                    : t("panel.zone.desc_ph", { defaultValue: "这个区是干什么的（可留空）" })}
                />
                <Button
                  tone="primary"
                  disabled={busy || (editing === "rename" && !String(editDraft || "").trim())}
                  onClick={() => { submitEdit(); }}
                >
                  {t("panel.zone.save", { defaultValue: "保存" })}
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => {
                    setEditing("");
                    setEditDraft("");
                  }}
                >
                  {t("panel.zone.cancel", { defaultValue: "取消" })}
                </Button>
              </Inline>
            ) : null}
            {armed && current ? (
              <Inline gap={8} align="center" wrap>
                <Text>
                  {t("panel.zone.remove_message", {
                    name: current.name,
                    count: current.total ?? 0,
                    defaultValue: "拆掉「{name}」会连带删它全部分类与 {count} 张图，不可恢复。防误删：等 3 秒。",
                  })}
                </Text>
                <Button
                  tone="danger"
                  disabled={busy || armLeft > 0}
                  onClick={() => {
                    setArmed(false);
                    props.onRemove?.(current.id, current.name);
                  }}
                >
                  {armLeft > 0
                    ? t("panel.zone.remove_wait", { sec: armLeft, defaultValue: "确认拆区（{sec}s）" })
                    : t("panel.zone.remove_now", { defaultValue: "确认拆区" })}
                </Button>
                <Button disabled={busy} onClick={() => { setArmed(false); }}>
                  {t("panel.zone.cancel", { defaultValue: "取消" })}
                </Button>
              </Inline>
            ) : null}
          </div>
        </details>
      </div>
      {current?.desc ? <p className="sticker-zonehead-desc">{current.desc}</p> : null}
    </div>
  );
}
