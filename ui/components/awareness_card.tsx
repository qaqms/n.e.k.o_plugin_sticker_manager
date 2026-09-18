// 存在感注入卡（v0.10.1 拆分自 panel.tsx）：读 context 的 awareness 读数 +
// 调试入口 `awareness_now`（绕节奏不绕开关）。ping 的反馈文案是本地状态，
// 整块自治，不进库卡模型——两件事没有对偶，别硬并。
//
// 联动纪律（陷阱 16）：注入必须随总开关冻结；心跳相反不冻结。这里只是读数与按钮，
// 尺在 Python 侧（services/awareness.py）。

import {
  Button,
  Card,
  Field,
  Inline,
  KeyValue,
  Select,
  Stack,
  Text,
  useState,
} from "@neko/plugin-ui";
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

  const setEagerness = async (next: string) => {
    setAwarenessNote("");
    try {
      await callAction(surface, "set_eagerness", { eagerness: next });
      // 档位是配置：写成功后 refresh 让 Select 回填服务端真值（不拿本地乐观值）。
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setAwarenessNote(t(`panel.error.${code}`, { defaultValue: code }));
    }
  };

  const awareness = state.awareness || {};
  // 节奏读数：只展示、不在面板上改——旋钮在配置文件（本轮定的是"先不加旋钮"）。
  const cadenceKey = `panel.awareness.cadence.${String(awareness.inject_mode || "interval_n")}`;
  const cadence = t(cadenceKey, {
    defaultValue: "每 {n} 轮 · 已攒 {since} 轮",
  })
    .replace("{n}", String(awareness.inject_interval_n || 1))
    .replace(
      "{since}",
      String(
        (awareness.turns_since_inject && awareness.target
          ? awareness.turns_since_inject[awareness.target]
          : undefined) ?? 0
      )
    );
  const driverKey = `panel.awareness.driver.${String(awareness.driver || "")}`;

  return (
    <Card title={t("panel.awareness.title", { defaultValue: "存在感注入" })}>
      <Stack gap={8}>
        <Text>
          {t("panel.awareness.note", {
            defaultValue:
              "在她每开新一轮时把『你有一间表情收藏间 + 最近常用的几张』静默注进她的上下文：你看不到、她不会因此开口。",
          })}
        </Text>
        <Field
          label={t("panel.awareness.eagerness", {
            defaultValue: "配表情积极度",
          })}
          help={t("panel.awareness.eagerness.help", {
            defaultValue:
              "只管她有多想配图。冷却、最近不重复、概率闸不吃这一档——那些在下面的配置里。",
          })}
        >
          <Select
            value={String(state.eagerness || "natural")}
            options={[
              {
                value: "reserved",
                label: t("panel.eagerness.reserved", {
                  defaultValue: "矜持：没有正合适的就不发",
                }),
              },
              {
                value: "natural",
                label: t("panel.eagerness.natural", {
                  defaultValue: "自然：贴切就发（默认）",
                }),
              },
              {
                value: "eager",
                label: t("panel.eagerness.eager", {
                  defaultValue: "爱发：情绪对得上就配一张",
                }),
              },
            ]}
            onChange={(next: any) => {
              setEagerness(String(next || "natural"));
            }}
          />
        </Field>
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
              {
                key: "cadence",
                label: t("panel.awareness.cadenceLabel", {
                  defaultValue: "节奏",
                }),
                value: cadence,
              },
              {
                key: "driver",
                label: t("panel.awareness.driverLabel", {
                  defaultValue: "驱动",
                }),
                value: t(driverKey, { defaultValue: awareness.driver || "—" }),
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
