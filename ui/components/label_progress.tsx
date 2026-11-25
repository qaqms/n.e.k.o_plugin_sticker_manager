// 自动识图进度（第 2 阶段）：识图在插件后台跑，这里只发指令、看进度。
// 三种摆法共用一份文案和按钮：
// - strip：四个标签页顶部的一行进度条；
// - card：概览里的大进度卡（带正在看的那张缩略图）；
// - inline：导入结果卡里的那一行。
// 面板在 status=running 时每 2 秒刷新一次（轮询在 panel.tsx，见 LABEL_POLL_MS）。
import { Button, useRef, useState } from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import { callAction, extractCode, formatEta, labelingVisible } from "../shared";
import type { LabelingState, Surface, Translate } from "../shared";

export const LABEL_POLL_MS = 2000;

// 一个面板只要一份：按钮忙闲、出错提示都在这里，三种摆法共享。
export function useLabelControl(surface: Surface, hooks?: {
  onRefreshFailed?: () => void;
  onRefreshRecovered?: () => void;
}) {
  const t = surface.t;
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const busyRef = useRef("");
  const run = async (op: string, args: Record<string, unknown> = {}) => {
    if (busyRef.current) return null;
    busyRef.current = op;
    setBusy(op);
    setNote("");
    try {
      const result = await callAction(surface, "auto_label", { op, ...args });
      try {
        await surface.api.refresh();
        hooks?.onRefreshRecovered?.();
      } catch {
        hooks?.onRefreshFailed?.();
      }
      return result;
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error ?? "failed");
      const code = extractCode(raw);
      setNote(t(`panel.label.error.${code}`, {
        defaultValue: t(`panel.error.${code}`, { defaultValue: code }),
      }));
      return null;
    } finally {
      busyRef.current = "";
      setBusy("");
    }
  };
  return { busy, note, clearNote: () => setNote(""), run };
}

export type LabelControl = ReturnType<typeof useLabelControl>;

export function LabelProgress(props: {
  key?: string;
  surface: Surface;
  control: LabelControl;
  variant: "strip" | "card" | "inline";
  disabled?: boolean;
  onShowFailed?: () => void;
  onGoSettings?: () => void;
}) {
  const t = props.surface.t;
  const labeling: LabelingState = props.surface.state?.labeling || {};
  if (!labelingVisible(labeling)) {
    return props.control.note
      ? <p className="sticker-label-note" role="status">{props.control.note}</p>
      : null;
  }
  const view = describe(t, labeling);
  const busy = !!props.control.busy || !!props.disabled;
  const button = (op: string, label: string, tone = "default", args?: Record<string, unknown>) => (
    <Button
      key={op}
      tone={tone as any}
      disabled={busy}
      onClick={() => { props.control.run(op, args); }}
    >
      {label}
    </Button>
  );
  const actions: any[] = [];
  if (view.kind === "running") {
    actions.push(button("pause", t("panel.label.pause", { defaultValue: "暂停" })));
  } else if (view.kind === "paused") {
    if (labeling.reason === "daily_cap" && props.onGoSettings) {
      actions.push(
        <Button key="settings" disabled={busy} onClick={props.onGoSettings}>
          {t("panel.label.go_settings", { defaultValue: "去设置" })}
        </Button>,
      );
    } else if (labeling.reason !== "quota" && labeling.reason !== "pil_unavailable") {
      actions.push(button(
        "resume",
        labeling.reason === "model"
          ? t("panel.label.retry", { defaultValue: "重试" })
          : t("panel.label.resume", { defaultValue: "继续" }),
        "primary",
      ));
    }
    actions.push(button("clear", t("panel.label.clear", { defaultValue: "清空队列" })));
  } else if (view.kind === "finished") {
    if (Number(labeling.failed_total || 0) > 0) {
      actions.push(button("retry_failed", t("panel.label.retry_failed", { defaultValue: "重试失败的" })));
      if (props.onShowFailed) {
        actions.push(
          <Button key="show" disabled={busy} onClick={props.onShowFailed}>
            {t("panel.label.show_failed", { defaultValue: "查看失败的" })}
          </Button>,
        );
      }
    }
    actions.push(button("dismiss", t("panel.label.dismiss", { defaultValue: "知道了" })));
  } else if (view.kind === "unsupported") {
    actions.push(button("clear", t("panel.label.clear", { defaultValue: "清空队列" })));
  }
  const bar = view.kind === "running" || view.kind === "paused" ? (
    <div
      className="sticker-label-bar"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={view.total}
      aria-valuenow={view.done}
    >
      <i style={{ width: `${view.percent}%` }} />
    </div>
  ) : null;
  const message = props.control.note
    ? <p className="sticker-label-note" role="status">{props.control.note}</p>
    : null;

  if (props.variant === "card") {
    return (
      <section className={`sticker-label-card is-${view.tone}`} aria-live="polite">
        {labeling.working_id ? <NowThumb surface={props.surface} id={labeling.working_id} /> : null}
        <div className="sticker-label-card-body">
          <h2 className="sticker-section-title">{view.title}</h2>
          {view.meta ? <p className="sticker-muted">{view.meta}</p> : null}
          {bar}
          <div className="sticker-label-nums">
            <span>{t("panel.label.ok_count", { count: view.ok, defaultValue: "成功 {count}" })}</span>
            <span>{t("panel.label.failed_count", { count: view.failed, defaultValue: "失败 {count}" })}</span>
            <span>{t("panel.label.left_count", { count: view.left, defaultValue: "剩余 {count}" })}</span>
            <span>
              {t("panel.label.today", {
                count: labeling.day_count ?? 0,
                cap: labeling.daily_cap ?? 300,
                defaultValue: "今天已用 {count} / {cap}",
              })}
            </span>
          </div>
          {actions.length ? <div className="sticker-label-actions">{actions}</div> : null}
          {message}
        </div>
      </section>
    );
  }
  return (
    <div className={`sticker-label-strip is-${view.tone}${props.variant === "inline" ? " is-inline" : ""}`} aria-live="polite">
      <span className="sticker-label-title">{view.title}</span>
      {bar}
      {view.meta ? <span className="sticker-label-meta">{view.meta}</span> : null}
      {actions.length ? <div className="sticker-label-actions">{actions}</div> : null}
      {message}
    </div>
  );
}

// 以下放在导出之后（旧宿主模块拼接器的限制，见 tests/test_hosted_ui_module_layout.py）。

function describe(t: Translate, labeling: LabelingState) {
  const run = labeling.run || {};
  const ok = Number(run.done || 0);
  const failed = Number(run.failed || 0);
  const total = Math.max(Number(run.total || 0), ok + failed);
  const done = ok + failed;
  const left = Math.max(0, Number(labeling.queued || 0) + (labeling.working_id ? 1 : 0));
  const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const status = String(labeling.status || "");
  const base = { ok, failed, total, done, left, percent };
  if (status === "running") {
    return {
      ...base, kind: "running", tone: "running",
      title: t("panel.label.running", { done, total, defaultValue: "正在识图 {done} / {total}" }),
      meta: formatEta(t, labeling.eta_sec),
    };
  }
  if (status === "unsupported") {
    return {
      ...base, kind: "unsupported", tone: "halt",
      title: t("panel.label.unsupported", { defaultValue: "当前宿主版本不支持自动识图" }),
      meta: t("panel.label.unsupported_sub", { defaultValue: "更新 N.E.K.O 后可用；手动编辑不受影响" }),
    };
  }
  if (status === "finished") {
    return {
      ...base, kind: "finished", tone: "done",
      title: t("panel.label.finished", { ok, failed, defaultValue: "识图完成：{ok} 张成功，{failed} 张失败" }),
      meta: "",
    };
  }
  const reason = String(labeling.reason || "user");
  if (reason === "user") {
    return {
      ...base, kind: "paused", tone: "paused",
      title: t("panel.label.paused", { done, total, defaultValue: "已暂停 {done} / {total}" }),
      meta: "",
    };
  }
  const code = String(labeling.last_error || "");
  const titles: Record<string, [string, string]> = {
    quota: ["今天的免费额度用完了", "还剩 {left} 张，明天会自动继续"],
    daily_cap: ["今天已识别 {count} 张（每日上限）", "明天自动继续，可在设置里调高"],
    model: ["识图模型不可用", "{detail}"],
    failures: ["连续 5 张没识别成功，先停一下", "可能是网络或模型暂时不稳定"],
    pil_unavailable: ["宿主缺少图片处理组件，无法识图", "更新 N.E.K.O 后可用"],
    unsupported: ["当前宿主版本不支持自动识图", "更新 N.E.K.O 后可用；手动编辑不受影响"],
  };
  const pair = titles[reason] || titles.failures;
  const detail = code
    ? t(`panel.selection.status.${code}`, { defaultValue: code })
    : t("panel.label.model_hint", { defaultValue: "在宿主里给“自动标注（识图）”绑定能看图的模型" });
  return {
    ...base, kind: "paused", tone: "halt",
    title: t(`panel.label.reason.${reason}`, { count: labeling.day_count ?? 0, left, defaultValue: pair[0] }),
    meta: t(`panel.label.reason.${reason}_sub`, { left, detail, defaultValue: pair[1] }),
  };
}

function NowThumb(props: { surface: Surface; id: string }) {
  const { preview, boxRef } = useStickerPreview(props.surface, props.id, false, "thumb");
  return (
    <div className="sticker-label-now" ref={boxRef}>
      {preview ? <img src={preview} alt="" /> : <span aria-hidden="true">…</span>}
    </div>
  );
}
