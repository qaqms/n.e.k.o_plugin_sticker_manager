// 图库左侧目录（第 2 阶段改成两层）：上面是收藏区，下面是这个区的分类。
// - 点区只是浏览，不会换掉猫娘正在用的区（实心点 ● 标出她在用的那个）；
// - 分类的值约定不变：""=全部，"group:<名字>"=某分类（未分组的名字是空串），
//   另有 "missing"=缺描述、"label_failed"=自动识图失败（有图才出现）。
// 窄屏下收藏区一节隐藏（改用区标题行里的下拉），分类变成横向滚动条。
import { useState } from "@neko/plugin-ui";
import type { Section, Surface, ZoneInfo } from "../shared";
import { NewItemForm } from "./new_item_form";

export const MISSING_FILTER = "missing";
// 自动识图 3 次都没成功的图（有才出现）。
export const FAILED_FILTER = "label_failed";

export function CategoryNav(props: {
  surface: Surface;
  sections: Section[];
  total: number;
  missing: number;
  failed?: number;
  value: string;
  disabled?: boolean;
  zones?: ZoneInfo[];
  view?: string;
  activeZone?: string;
  onSwitchZone?: (zoneId: string) => void;
  onCreateZone?: (name: string, desc: string) => Promise<boolean>;
  onChange: (next: string) => void;
  onCreate: () => void;
}) {
  const t = props.surface.t;
  const [creatingZone, setCreatingZone] = useState(false);
  const zones = props.zones || [];
  const current = zones.filter((zone) => zone.id === props.view)[0];
  const item = (value: string, label: string, count: number, extra = "") => (
    <button
      key={value || "__all__"}
      type="button"
      className={`sticker-nav-item${extra}`}
      aria-current={props.value === value ? "true" : "false"}
      onClick={() => props.onChange(value)}
    >
      <span className="sticker-nav-name">{label}</span>
      <span className="sticker-nav-count">{String(count)}</span>
    </button>
  );
  const categories = (
    <nav className="sticker-catnav" aria-label={t("panel.category.filter", { defaultValue: "分类" })}>
      {item("", t("panel.category.all", { defaultValue: "全部分类" }), props.total)}
      {props.sections.map((section) => item(`group:${section.group}`, section.name, section.total))}
      {props.missing > 0
        ? item(MISSING_FILTER, t("panel.category.missing", { defaultValue: "缺描述" }), props.missing, " is-attention")
        : null}
      {(props.failed || 0) > 0
        ? item(FAILED_FILTER, t("panel.category.label_failed", { defaultValue: "识别失败" }), props.failed || 0, " is-danger")
        : null}
      <button
        type="button"
        className="sticker-nav-item is-create"
        disabled={props.disabled}
        onClick={props.onCreate}
      >
        <span className="sticker-nav-name">
          {t("panel.group.new_button_plus", { defaultValue: "＋ 新建分类" })}
        </span>
      </button>
    </nav>
  );
  if (!zones.length) return categories;
  return (
    <div className="sticker-sidebar">
      <section className="sticker-zonelist" aria-label={t("panel.zone.browse", { defaultValue: "收藏区" })}>
        <h3 className="sticker-side-title">{t("panel.zone.browse", { defaultValue: "收藏区" })}</h3>
        <p className="sticker-side-hint">
          {t("panel.zone.live_hint", { defaultValue: "● = 猫娘正在用的区" })}
        </p>
        {zones.map((zone) => (
          <button
            key={zone.id}
            type="button"
            className="sticker-nav-item sticker-zone-item"
            aria-current={zone.id === props.view ? "true" : "false"}
            disabled={props.disabled && zone.id !== props.view}
            onClick={() => props.onSwitchZone?.(zone.id)}
          >
            <span className={`sticker-zone-dot${zone.id === props.activeZone ? " is-live" : ""}`} aria-hidden="true" />
            <span className="sticker-nav-name">
              {zone.name}
              {zone.id === props.activeZone ? (
                <span className="sticker-sr-only">
                  {t("panel.zone.in_use_short", { defaultValue: "（猫娘正在用）" })}
                </span>
              ) : null}
            </span>
            <span className="sticker-nav-count">{String(zone.total ?? 0)}</span>
          </button>
        ))}
        {creatingZone && props.onCreateZone ? (
          <NewItemForm
            surface={props.surface}
            busy={props.disabled}
            namePlaceholder={t("panel.zone.name_ph_short", { defaultValue: "区名字，如：节日限定" })}
            descPlaceholder={t("panel.zone.desc_ph_short", { defaultValue: "一句说明（可选）" })}
            submitLabel={t("panel.zone.create_short", { defaultValue: "新建" })}
            onSubmit={async (name, desc) => {
              const ok = await props.onCreateZone!(name, desc);
              if (ok) setCreatingZone(false);
              return ok;
            }}
            onCancel={() => setCreatingZone(false)}
          />
        ) : (
          <button
            type="button"
            className="sticker-nav-item is-create"
            disabled={props.disabled}
            onClick={() => setCreatingZone(true)}
          >
            <span className="sticker-nav-name">
              {t("panel.zone.new_plus", { defaultValue: "＋ 新建收藏区" })}
            </span>
          </button>
        )}
      </section>
      <h3 className="sticker-side-title sticker-side-cats">
        {t("panel.category.of_zone", { name: current?.name || "", defaultValue: "分类 · {name}" })}
      </h3>
      {categories}
    </div>
  );
}
