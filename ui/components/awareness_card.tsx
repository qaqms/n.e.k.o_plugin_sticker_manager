// 存在感注入卡（v0.10.1 拆分自 panel.tsx）：读 context 的 awareness 读数 +
// 调试入口 `awareness_now`（绕节奏不绕开关）。ping 的反馈文案是本地状态，
// 整块自治，不进库卡模型——两件事没有对偶，别硬并。
//
// 联动纪律（陷阱 16）：注入必须随总开关冻结；心跳相反不冻结。这里只是读数与按钮，
// 尺在 Python 侧（services/awareness.py）。

import { Button, Card, Inline, KeyValue, Stack, Text, useState } from "@neko/plugin-ui";
import { callAction, extractCode } from "../shared";
import type { Surface } from "../shared";

export function AwarenessCard(props: { surface: Surface }) {
  const surface = props.surface;
  const t = surface.t;
  const state = surface.state || {};
  const [awarenessNote, setAwarenessNote] = useState("");

  const ping = async () => {
    setAwarenessNote("");
    try {
      const result = await callAction(surface, "awareness_now", {});
      if (result) {
        setAwarenessNote(
          t(`panel.awareness.status.${String(result.status || "")}`, {
            defaultValue: String(result.status || ""),
          }),
        );
      }
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setAwarenessNote(t(`panel.error.${code}`, { defaultValue: code }));
    }
  };

  return (
    <Card title={t("panel.awareness.title", { defaultValue: "存在感注入" })}>
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
  );
}
