// 分类区块（0.9.7 面板重构）：说明和操作直接摆在标题行下，不再折进“更多”菜单。
// 选择模式下才出现“全选本组”；拖文件到区块上仍会收进这个分类。
import { Button, Field, Inline, Input, Stack, Text } from "@neko/plugin-ui";
import { StickerTile } from "./sticker_tile";
import { useFileDrop } from "../file_drop";
import type { Section, Surface } from "../shared";

export function CategorySection(props: {
  key?: string;
  surface: Surface;
  section: Section;
  selected: string[];
  selecting?: boolean;
  activeId?: string;
  collectBusy: boolean;
  disabled?: boolean;
  descEditing: string;
  descDraft: string;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onCollect: () => void;
  onDropFiles?: (files: any[]) => void;
  onSelectAll: () => void;
  onToggleDescEditing: () => void;
  setDescDraft: (next: string) => void;
  onSaveDesc: () => void;
  onCancelDesc: () => void;
  onRemoveCategory: () => void;
}) {
  const t = props.surface.t;
  const section = props.section;
  const editingDesc = section.editable && props.descEditing === section.group;
  const drop = useFileDrop((files) => props.onDropFiles?.(files), !!props.disabled || props.collectBusy);
  return (
    <section className={`sticker-category${drop.dragging ? " is-file-dragging" : ""}`} {...drop.handlers}>
      <div className="sticker-category-header">
        <h2 className="sticker-category-name">
          {section.name}
          <span className="sticker-category-count">
            {section.rows.length === section.total
              ? String(section.total)
              : `${section.rows.length} / ${section.total}`}
          </span>
        </h2>
        <div className="sticker-category-actions">
          {props.selecting && section.rows.length ? (
            <Button tone="default" disabled={props.disabled} onClick={props.onSelectAll}>
              {t("panel.section.select_all", { defaultValue: "全选本组" })}
            </Button>
          ) : null}
          {section.editable ? (
            <Button tone="default" disabled={props.disabled} onClick={props.onToggleDescEditing}>
              {t("panel.section.desc_edit", { defaultValue: "编辑说明" })}
            </Button>
          ) : null}
          {section.editable ? (
            <Button tone="danger" disabled={props.disabled} onClick={props.onRemoveCategory}>
              {t("panel.group.delete_button", { defaultValue: "删分类" })}
            </Button>
          ) : null}
          <Button tone="default" disabled={props.disabled || props.collectBusy} onClick={props.onCollect}>
            {props.collectBusy
              ? t("panel.collect.busy_one", { defaultValue: "收藏中…" })
              : t("panel.collect.button", { defaultValue: "添加图片" })}
          </Button>
        </div>
      </div>
      {editingDesc ? (
        <Stack gap={8}>
          <Field label={t("panel.group.description", { defaultValue: "分类说明" })}>
            <Input
              value={props.descDraft}
              disabled={props.disabled}
              onChange={props.setDescDraft}
            />
          </Field>
          <Inline gap={8} wrap>
            <Button tone="primary" disabled={props.disabled} onClick={props.onSaveDesc}>
              {t("panel.group.desc_save", { defaultValue: "存分组说明" })}
            </Button>
            <Button disabled={props.disabled} onClick={props.onCancelDesc}>
              {t("panel.section.desc_cancel", { defaultValue: "取消" })}
            </Button>
          </Inline>
        </Stack>
      ) : section.desc ? (
        <p className="sticker-category-desc">{section.desc}</p>
      ) : section.editable ? (
        <p className="sticker-category-desc is-empty">
          {t("panel.category.no_desc", { defaultValue: "还没有分类说明。选图模型会参考这句话判断什么时候用这一类。" })}
        </p>
      ) : null}
      {section.total === 0 ? (
        <Text>{t("panel.category.empty", { defaultValue: "暂无表情包" })}</Text>
      ) : null}
      <div className="sticker-grid">
        {section.rows.map((row) => (
          <StickerTile
            key={row.id}
            row={row}
            surface={props.surface}
            selected={props.selected.indexOf(row.id) >= 0}
            selecting={props.selecting}
            active={props.activeId === row.id}
            disabled={props.disabled}
            onToggleSelect={() => props.onToggleSelect(row.id)}
            onOpen={() => props.onOpen(row.id)}
          />
        ))}
      </div>
    </section>
  );
}
