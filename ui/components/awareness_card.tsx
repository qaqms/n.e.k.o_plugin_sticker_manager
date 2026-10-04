// Sending preferences and diagnostics share actions, not a wall of explanatory text.
import {
  Alert,
  Button,
  Field,
  Inline,
  Select,
  Slider,
  Stack,
  Text,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui";
import { callAction, extractCode } from "../shared";
import type { Surface } from "../shared";

export function AwarenessCard(props: {
  surface: Surface;
  mode: "settings" | "status";
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onRefreshFailed?: () => void;
  onRefreshRecovered?: () => void;
}) {
  const surface = props.surface;
  const t = surface.t;
  const state = surface.state || {};
  const awareness = state.awareness || {};
  const runState = state.run || {};
  const configuredInterval = awareness.min_interval_sec ?? 60;
  const [intervalDraft, setIntervalDraft] = useState(configuredInterval);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState("");
  const busyRef = useRef(false);
  const disabled = !!props.disabled || !!pending;
  useEffect(() => {
    setIntervalDraft(configuredInterval);
  }, [configuredInterval]);

  const run = async (action: string, args: Record<string, unknown>) => {
    if (busyRef.current || props.disabled) return;
    busyRef.current = true;
    setPending(action);
    props.onBusyChange?.(true);
    setNote("");
    setFailed(false);
    try {
      const result = await callAction(surface, action, args);
      if (action === "set_reminder_interval") {
        if (result?.min_interval_sec !== undefined) setIntervalDraft(result.min_interval_sec);
        setNote(t("panel.awareness.interval.saved", { defaultValue: "提醒间隔已保存" }));
      } else if (action === "awareness_now" && result) {
        const status = String(result.status || "");
        setNote(t(`panel.awareness.status.${status}`, { defaultValue: status }));
      }
      try {
        await surface.api.refresh();
        props.onRefreshRecovered?.();
      } catch {
        props.onRefreshFailed?.();
      }
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setFailed(true);
      setNote(t(`panel.error.${code}`, { defaultValue: code }));
    } finally {
      busyRef.current = false;
      setPending("");
      props.onBusyChange?.(false);
    }
  };
  const cadence = t(`panel.awareness.cadence.${String(awareness.inject_mode || "interval_n")}`, {
    defaultValue: "提醒门槛 {n} 轮 · 距上次提醒 {since} 轮",
  })
    .replace("{n}", String(awareness.inject_interval_n || 1))
    .replace("{since}", String(awareness.turns_since ?? 0));
  const statusItems = [
    {
      key: "status",
      label: t("panel.awareness.status", { defaultValue: "最近一次" }),
      value: t(`panel.awareness.status.${String(awareness.status || "")}`, {
        defaultValue: awareness.status || "—",
      }),
    },
    {
      key: "target",
      label: t("panel.awareness.target", { defaultValue: "注给" }),
      value: awareness.target || "—",
    },
    {
      key: "next",
      label: t("panel.awareness.next", { defaultValue: "下次最快" }),
      value: `${Math.ceil(awareness.min_next_wait_sec || 0)}s`,
    },
    {
      key: "cadence",
      label: t("panel.awareness.cadenceLabel", { defaultValue: "节奏" }),
      value: cadence,
    },
    {
      key: "driver",
      label: t("panel.awareness.driverLabel", { defaultValue: "驱动" }),
      value: t(`panel.awareness.driver.${String(awareness.driver || "")}`, {
        defaultValue: awareness.driver || "—",
      }),
    },
  ];
  const runItems = [
    { key: "turns", label: t("panel.run.turns", { defaultValue: "她的话轮" }), value: String(runState.turns || 0) },
    { key: "calls", label: t("panel.run.calls", { defaultValue: "她调用工具" }), value: String(runState.tool_calls || 0) },
    { key: "sent", label: t("panel.run.sent", { defaultValue: "发出成功" }), value: String(runState.sent || 0) },
    { key: "refused", label: t("panel.run.refused", { defaultValue: "被拦下" }), value: String(runState.refused || 0) },
    {
      key: "surface",
      label: t("panel.run.surface", { defaultValue: "常驻目录" }),
      value: String(runState.surface_categories || 0) + t("panel.run.surface.unit", { defaultValue: " 类" }),
    },
  ];
  const libraryItems = [
    { key: "total", label: t("panel.stat.total", { defaultValue: "收藏" }), value: String(state.counts?.total || 0) },
    { key: "available", label: t("panel.stat.available", { defaultValue: "可用" }), value: String(state.counts?.enabled || 0) },
    { key: "sent", label: t("panel.stat.sent", { defaultValue: "累计发出" }), value: String(state.counts?.sent_total || 0) },
    { key: "groups", label: t("panel.category.filter", { defaultValue: "分类" }), value: String(state.counts?.groups || 0) },
  ];
  const readouts = (items: Array<{ key: string; label: string; value: string }>) => (
    <dl className="sticker-readouts">
      {items.map((item) => (
        <div key={item.key}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );

  return (
    <div className="sticker-settings">
      {note ? <Alert tone={failed ? "danger" : "info"} message={note} /> : null}
      {props.mode === "settings" ? (
        <Stack gap={16}>
          <Field label={t("panel.awareness.eagerness", { defaultValue: "配表情积极度" })}>
            <Select
              disabled={disabled}
              value={String(state.eagerness || "natural")}
              options={[
                { value: "reserved", label: t("panel.eagerness.reserved", { defaultValue: "矜持：没有正合适的就不发" }) },
                { value: "natural", label: t("panel.eagerness.natural", { defaultValue: "自然：贴切就发（默认）" }) },
                { value: "eager", label: t("panel.eagerness.eager", { defaultValue: "爱发：情绪对得上就配一张" }) },
              ]}
              onChange={(next: any) => {
                run("set_eagerness", { eagerness: String(next || "natural") });
              }}
            />
          </Field>
          <Field label={t("panel.awareness.min_interval_sec", { defaultValue: "提醒最小间隔（秒）" })}>
            <Stack gap={8}>
              <Slider
                value={intervalDraft}
                min={0}
                max={60}
                step={1}
                showValue
                disabled={disabled}
                onChange={setIntervalDraft}
              />
              <Inline gap={12} wrap>
                <Text>
                  {intervalDraft === 0
                    ? t("panel.awareness.interval.off", { defaultValue: "无时间限制" })
                    : t("panel.awareness.interval.seconds", {
                      defaultValue: "最少间隔 {seconds} 秒",
                    }).replace("{seconds}", String(intervalDraft))}
                </Text>
                <Button
                  disabled={disabled || intervalDraft === configuredInterval}
                  onClick={() => {
                    run("set_reminder_interval", { min_interval_sec: intervalDraft });
                  }}
                >
                  {pending === "set_reminder_interval"
                    ? t("panel.awareness.interval.saving", { defaultValue: "保存中" })
                    : t("panel.awareness.interval.save", { defaultValue: "保存" })}
                </Button>
              </Inline>
            </Stack>
          </Field>
        </Stack>
      ) : (
        <Stack gap={16}>
          <h2 className="sticker-section-title">
            {t("panel.library.overview", { defaultValue: "图库概况" })}
          </h2>
          {readouts(libraryItems)}
          <h2 className="sticker-section-title">
            {t("panel.awareness.title", { defaultValue: "存在感注入" })}
          </h2>
          {readouts(statusItems)}
          <Inline gap={8}>
            <Button disabled={disabled} onClick={() => { run("awareness_now", {}); }}>
              {pending === "awareness_now"
                ? t("panel.awareness.checking", { defaultValue: "检查中…" })
                : t("panel.awareness.button", { defaultValue: "现在注一条（调试）" })}
            </Button>
          </Inline>
          <h2 className="sticker-section-title">
            {t("panel.run.title", { defaultValue: "本次运行（重启插件归零）" })}
          </h2>
          {readouts(runItems)}
          {runState.last_call ? (
            <Text>
              {t("panel.run.last", { defaultValue: "最近一次" }) + "：" + runState.last_call +
                (runState.last_reason ? " · " + runState.last_reason : "")}
            </Text>
          ) : null}
        </Stack>
      )}
    </div>
  );
}
