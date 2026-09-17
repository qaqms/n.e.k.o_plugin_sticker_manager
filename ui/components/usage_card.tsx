// 台账卡（v0.10.1 拆分自 panel.tsx）：她最近用过的——只读呈现，无任何动作。
// 台账只记时刻与表情 id，不含对话原文（隐私纪律）。

import { Card, DataTable, ScrollArea, StatusBadge, Text } from "@neko/plugin-ui";
import { formatTime } from "../shared";
import type { Translate, UsageRow } from "../shared";

export function UsageCard(props: { t: Translate; usage: UsageRow[] }) {
  const t = props.t;
  return (
    <Card title={t("panel.card.usage", { defaultValue: "她最近用过的" })}>
      <ScrollArea height={320}>
        <DataTable
          data={props.usage}
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
  );
}
