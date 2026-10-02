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
  Slider,
  Stack,
  Text,
  useState,
  useEffect,
} from "@neko/plugin-ui";
import { callAction, extractCode } from "../shared";
import type { Surface } from "../shared";

export function AwarenessCard(props: { surface: Surface }) {
  const surface = props.surface;
  const t = surface.t;
  const state = surface.state || {};
  const [awarenessNote, setAwarenessNote] = useState("");
  const configuredInterval = state.awareness?.min_interval_sec ?? 60;
  const [intervalDraft, setIntervalDraft] = useState(configuredInterval);
  const [intervalSaving, setIntervalSaving] = useState(false);
  useEffect(() => {
    setIntervalDraft(configuredInterval);
  }, [configuredInterval]);

  const saveInterval = async () => {
    if (intervalSaving || intervalDraft === configuredInterval) return;
    setIntervalSaving(true);
    setAwarenessNote("");
    try {
      const result = await callAction(surface, "set_reminder_interval", {
        min_interval_sec: intervalDraft,
      });
      if (result) {
        setIntervalDraft(result.min_interval_sec);
        setAwarenessNote(t("panel.awareness.interval.saved", {
          defaultValue: "提醒间隔已保存",
        }));
      }
      await surface.api.refresh();
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setAwarenessNote(t(`panel.error.${code}`, { defaultValue: code }));
    } finally {
      setIntervalSaving(false);
    }
  };

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
  // 轮数门槛仍随档位/配置，时间限制可独立调整。
  const cadenceKey = `panel.awareness.cadence.${String(awareness.inject_mode || "interval_n")}`;
  const cadence = t(cadenceKey, {
    defaultValue: "提醒门槛 {n} 轮 · 距上次提醒 {since} 轮",
  })
    .replace("{n}", String(awareness.inject_interval_n || 1))
    .replace("{since}", String(awareness.turns_since ?? 0));
  const driverKey = `panel.awareness.driver.${String(awareness.driver || "")}`;

  return (
    <Card title={t("panel.awareness.title", { defaultValue: "存在感注入" })}>
      <Stack gap={8}>
        <Text>
          {t("panel.awareness.note", {
            defaultValue:
              "在她每开新一轮时静默递一句『你有一间表情收藏间，想配就发一张』：你看不到、她不会因此开口。分类目录在 sticker_send 的说明里，每轮都在场。",
          })}
        </Text>
        <Field
          label={t("panel.awareness.eagerness", {
            defaultValue: "配表情积极度",
          })}
          help={t("panel.awareness.eagerness.help", {
            defaultValue:
              "她有多主动配图。这一档同时管三件事：每几轮点名提醒一次（矜持 12 / 自然 6 / 爱发 3）、工具描述里判据的强弱、以及提醒的措辞。冷却、最近不重复、概率闸一口都不吃这一档。想自己定节奏就在配置里把 inject_interval_n 写成具体数字（0 = 跟随档位）。",
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
        <Field
          label={t("panel.awareness.min_interval_sec", {
            defaultValue: "提醒最小间隔（秒）",
          })}
        >
          <Stack gap={8}>
            <Slider
              value={intervalDraft}
              min={0}
              max={60}
              step={1}
              showValue={true}
              disabled={intervalSaving}
              onChange={setIntervalDraft}
            />
            <Inline gap={16} align="center" wrap>
              <Text>
                {intervalDraft === 0
                  ? t("panel.awareness.interval.off", {
                      defaultValue: "无时间限制",
                    })
                  : t("panel.awareness.interval.seconds", {
                      defaultValue: "最少间隔 {seconds} 秒",
                    }).replace("{seconds}", String(intervalDraft))}
              </Text>
              <Button
                tone="default"
                disabled={intervalSaving || intervalDraft === configuredInterval}
                onClick={saveInterval}
              >
                {intervalSaving
                  ? t("panel.awareness.interval.saving", { defaultValue: "保存中" })
                  : t("panel.awareness.interval.save", { defaultValue: "保存" })}
              </Button>
            </Inline>
          </Stack>
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
        {/* v0.17.1 观测轮：本次运行的四把读数。只读，不放任何旋钮。
            分母是"开机到现在"，所以标题必须写"本次运行"——写成"今日"就是假账。 */}
        <Text>
          {t("panel.run.title", {
            defaultValue: "本次运行（重启插件归零）",
          })}
        </Text>
        <Inline gap={16} align="center" wrap>
          <KeyValue
            items={[
              {
                key: "turns",
                label: t("panel.run.turns", { defaultValue: "她的话轮" }),
                value: String((state.run && state.run.turns) || 0),
              },
              {
                key: "calls",
                label: t("panel.run.calls", { defaultValue: "她调用工具" }),
                value: String((state.run && state.run.tool_calls) || 0),
              },
              {
                key: "sent",
                label: t("panel.run.sent", { defaultValue: "发出成功" }),
                value: String((state.run && state.run.sent) || 0),
              },
              {
                key: "refused",
                label: t("panel.run.refused", { defaultValue: "被拦下" }),
                value: String((state.run && state.run.refused) || 0),
              },
              {
                key: "surface",
                label: t("panel.run.surface", { defaultValue: "常驻目录" }),
                value:
                  String((state.run && state.run.surface_categories) || 0) +
                  t("panel.run.surface.unit", { defaultValue: " 类" }),
              },
            ]}
          />
        </Inline>
        {state.run && state.run.last_call ? (
          <Text>
            {t("panel.run.last", { defaultValue: "最近一次" }) +
              "：" +
              String(state.run.last_call) +
              (state.run.last_reason
                ? " → " + String(state.run.last_reason)
                : "")}
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}
