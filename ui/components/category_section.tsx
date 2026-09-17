// 分类区块（轮 G 分区视图，v0.10.1 拆分自 panel.tsx）：一个分类一块——
// 块头「组名 · 张数 + 就地操作」，下面表情墙。收图长在块头（轮 I 2A），
// 删分类的确认数由模型侧摊服务端张数（陷阱 22）；这里只管摆。

import { Button, Divider, Inline, Input, Stack, Text } from "@neko/plugin-ui";
import { StickerTile } from "./sticker_tile";
import type { Section, Surface } from "../shared";

export function CategorySection(props: {
  key?: string;
  surface: Surface;
  section: Section;
  showDivider: boolean;
  selected: string[];
  collectBusy: boolean;
  descEditing: string;
  descDraft: string;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onCollect: () => void;
  onSelectAll: () => void;
  onToggleDescEditing: () => void;
  setDescDraft: (next: string) => void;
  onSaveDesc: () => void;
  onCancelDesc: () => void;
  onRemoveCategory: () => void;
}) {
  const t = props.surface.t;
  const section = props.section;
  return (
    <Stack gap={8}>
      {props.showDivider ? <Divider /> : null}
      <Inline gap={8} align="center" wrap>
        <Text>
          {section.name}（{section.rows.length}）
        </Text>
        {/* 轮 I（2A）：收图长在分类上——选文件即收，不再问逐图字段。 */}
        <Button
          tone="primary"
          disabled={props.collectBusy}
          onClick={() => {
            props.onCollect();
          }}
        >
          {props.collectBusy
            ? t("panel.collect.busy_one", { defaultValue: "收藏中…" })
            : t("panel.collect.button", { defaultValue: "收图进这一类" })}
        </Button>
        {section.rows.length ? (
          <Button
            tone="default"
            onClick={() => {
              props.onSelectAll();
            }}
          >
            {t("panel.section.select_all", { defaultValue: "全选本组" })}
          </Button>
        ) : null}
        {section.editable ? (
          <Button
            tone="default"
            onClick={() => {
              props.onToggleDescEditing();
            }}
          >
            {t("panel.section.desc_edit", { defaultValue: "编辑说明" })}
          </Button>
        ) : null}
        {section.editable ? (
          <Button
            tone="danger"
            onClick={() => {
              props.onRemoveCategory();
            }}
          >
            {t("panel.group.delete_button", { defaultValue: "删分类" })}
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
      {section.editable && props.descEditing !== section.group ? (
        <Text>
          {section.desc ||
            t("panel.section.desc_hint", {
              defaultValue:
                "未写说明——补一句『什么时候用这一组』，她目录里看到的分类正文就是它。",
            })}
        </Text>
      ) : null}
      {section.editable && props.descEditing === section.group ? (
        <Inline gap={6} align="center" wrap>
          <Input
            value={props.descDraft}
            onChange={props.setDescDraft}
            placeholder={t("panel.group.desc_ph", {
              defaultValue: "什么时候用这一组——她选图时看到的分类正文",
            })}
          />
          <Button
            tone="primary"
            onClick={() => {
              props.onSaveDesc();
            }}
          >
            {t("panel.group.desc_save", { defaultValue: "存分组说明" })}
          </Button>
          <Button
            tone="default"
            onClick={() => {
              props.onCancelDesc();
            }}
          >
            {t("panel.section.desc_cancel", { defaultValue: "取消" })}
          </Button>
        </Inline>
      ) : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(128px, 176px))",
          gap: 8,
        }}
      >
        {section.rows.map((row) => (
          <StickerTile
            key={row.id}
            row={row}
            surface={props.surface}
            selected={props.selected.indexOf(row.id) >= 0}
            onToggleSelect={() => {
              props.onToggleSelect(row.id);
            }}
            onOpen={() => {
              props.onOpen(row.id);
            }}
          />
        ))}
      </div>
    </Stack>
  );
}
