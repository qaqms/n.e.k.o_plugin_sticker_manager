// 图库左侧目录（0.9.7 面板重构）：一眼看到当前区的全部分类，点哪类右边就只显示哪类。
// 值的约定与旧筛选下拉框一致：""=全部，"group:<名字>"=某分类（未分组的名字是空串），
// 另加 "missing"=缺描述（没有梗义也没有标签）。窄屏下同一列表改为横向滚动条。
import type { Section, Surface } from "../shared";

export const MISSING_FILTER = "missing";

export function CategoryNav(props: {
  surface: Surface;
  sections: Section[];
  total: number;
  missing: number;
  value: string;
  disabled?: boolean;
  onChange: (next: string) => void;
  onCreate: () => void;
}) {
  const t = props.surface.t;
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
  return (
    <nav className="sticker-catnav" aria-label={t("panel.category.filter", { defaultValue: "分类" })}>
      {item("", t("panel.category.all", { defaultValue: "全部分类" }), props.total)}
      {props.sections.map((section) => item(`group:${section.group}`, section.name, section.total))}
      {props.missing > 0
        ? item(MISSING_FILTER, t("panel.category.missing", { defaultValue: "缺描述" }), props.missing, " is-attention")
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
}
