// 批量条（v0.7.0 轮 F，v0.10.1 拆分自 panel.tsx）：勾选后的整排操作。
// 归类只给 Select（轮 I：手打新名字会静默立隐式分类）；删除前先摊精确张数再问。

import { Button, Inline, Input, Select, Text } from "@neko/plugin-ui";
import { categoryOptions } from "../shared";
import type { Surface } from "../shared";

export function BatchBar(props: {
  surface: Surface;
  selected: string[];
  batchTags: string;
  setBatchTags: (next: string) => void;
  batchGroup: string;
  setBatchGroup: (next: string) => void;
  onRun: (patch: Record<string, unknown>) => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  const t = props.surface.t;
  if (!props.selected.length) {
    return null;
  }
  return (
    <Inline gap={6} align="center" wrap>
      <Text>
        {t("panel.batch.selected", {
          count: props.selected.length,
          defaultValue: "已选 {count} 张",
        })}
      </Text>
      <Input
        value={props.batchTags}
        onChange={props.setBatchTags}
        placeholder={t("panel.batch.tags_ph", {
          defaultValue: "标签，逗号分隔",
        })}
      />
      <Button
        tone="default"
        onClick={() => {
          props.onRun({ tags_add: props.batchTags });
        }}
      >
        {t("panel.batch.add_tags", { defaultValue: "加标签" })}
      </Button>
      <Button
        tone="default"
        onClick={() => {
          props.onRun({ tags_remove: props.batchTags });
        }}
      >
        {t("panel.batch.remove_tags", { defaultValue: "删标签" })}
      </Button>
      <Text>
        {t("panel.group.pick", { defaultValue: "选已有分类" })}
      </Text>
      <Select
        value={props.batchGroup}
        options={categoryOptions(props.surface, t)}
        onChange={(next: any) => {
          props.setBatchGroup(
            String(next === undefined || next === null ? "" : next),
          );
        }}
      />
      <Button
        tone="default"
        onClick={() => {
          props.onRun({ group: props.batchGroup });
        }}
      >
        {t("panel.batch.move", { defaultValue: "移入分类" })}
      </Button>
      <Button
        tone="default"
        onClick={() => {
          props.onRun({ disabled: false });
        }}
      >
        {t("panel.batch.enable", { defaultValue: "启用" })}
      </Button>
      <Button
        tone="default"
        onClick={() => {
          props.onRun({ disabled: true });
        }}
      >
        {t("panel.batch.disable", { defaultValue: "禁用" })}
      </Button>
      <Button
        tone="danger"
        onClick={() => {
          props.onDelete();
        }}
      >
        {t("panel.batch.delete", { defaultValue: "删除所选" })}
      </Button>
      <Button
        tone="default"
        onClick={() => {
          props.onClear();
        }}
      >
        {t("panel.batch.clear", { defaultValue: "取消选择" })}
      </Button>
    </Inline>
  );
}
