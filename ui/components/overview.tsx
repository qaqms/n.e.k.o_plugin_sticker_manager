// 概览页（0.9.7 面板重构）：打开面板先回答“插件在不在正常干活”，再列出需要主人处理的事。
// 只读为主；唯一的写操作是“测试选图”，其余按钮都只是跳到对应页面。
import { Alert, Button, useRef, useState } from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import { callAction, extractCode, needsDescription, selectionHealth, SELECTION_FAILURES } from "../shared";
import type { StickerRow, Surface } from "../shared";

const RECENT_LIMIT = 8;

export function OverviewPage(props: {
  surface: Surface;
  disabled?: boolean;
  onOpenSticker: (id: string) => void;
  onShowMissing: () => void;
  onGo: (tab: string) => void;
  onRefreshFailed?: () => void;
  onRefreshRecovered?: () => void;
}) {
  const surface = props.surface;
  const t = surface.t;
  const state = surface.state || {};
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [testing, setTesting] = useState(false);
  const busyRef = useRef(false);
  const health = selectionHealth(state, t);
  const stickers = state.stickers || [];
  const zones = state.zones || [];
  const activeId = String(state.active_zone || "");
  const active = zones.filter((zone) => zone.id === activeId)[0];
  const activeRows = stickers.filter((row) => String(row.zone || "") === activeId);
  const activeGroups = (state.groups || []).filter((group) => String(group.zone || "") === activeId);
  const missing = activeRows.filter((row) => !row.disabled && needsDescription(row)).length;
  const probability = Math.round((state.config?.probability ?? 0.5) * 100);
  const run = state.run || {};
  const attachment = state.eager_attachment || {};

  const seen: Record<string, boolean> = {};
  const recent: StickerRow[] = [];
  (state.usage || []).forEach((entry) => {
    const id = String(entry?.id || "");
    if (!id || entry?.ok === false || seen[id] || recent.length >= RECENT_LIMIT) return;
    const row = stickers.filter((item) => item.id === id)[0];
    if (!row) return;
    seen[id] = true;
    recent.push(row);
  });

  const testSelection = async () => {
    if (busyRef.current || props.disabled) return;
    busyRef.current = true;
    setTesting(true);
    setNote("");
    setFailed(false);
    try {
      const result = await callAction(surface, "test_selection", {});
      const status = String(result?.status || "");
      setNote(t(`panel.selection.status.${status}`, { defaultValue: status }));
      setFailed(SELECTION_FAILURES.includes(status));
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
      setTesting(false);
    }
  };

  const probabilityHint = probability === 0
    ? t("panel.send.probability.off", { defaultValue: "不自动配图" })
    : probability === 100
      ? t("panel.send.probability.every", { defaultValue: "每轮都选图" })
      : t("panel.overview.probability_hint", {
          turns: String(Math.round(100 / probability * 10) / 10),
          defaultValue: "约每 {turns} 轮一张",
        });

  const todo: Array<{ key: string; text: string; action?: any }> = [];
  if (!state.enabled) {
    todo.push({
      key: "off",
      text: t("panel.overview.todo.off", { defaultValue: "发表情包已关闭，猫娘不会发图。用右上角的开关打开。" }),
    });
  }
  if (health.tone === "warn") {
    todo.push({
      key: "selection",
      text: health.detail,
      action: health.canTest ? (
        <Button disabled={!!props.disabled || testing} onClick={() => { testSelection(); }}>
          {testing
            ? t("panel.overview.testing", { defaultValue: "测试中…" })
            : t("actions.test_selection.label", { defaultValue: "测试选图" })}
        </Button>
      ) : null,
    });
  }
  if (missing > 0) {
    todo.push({
      key: "missing",
      text: t("panel.overview.todo.missing", {
        count: missing,
        defaultValue: "使用区里有 {count} 张图没有梗义和标签，选图模型只能看到文件名，很难选中它们。",
      }),
      action: (
        <Button disabled={!!props.disabled} onClick={props.onShowMissing}>
          {t("panel.overview.todo.missing_go", { defaultValue: "去补描述" })}
        </Button>
      ),
    });
  }
  if (!activeRows.length) {
    todo.push({
      key: "empty",
      text: t("panel.overview.todo.empty", { defaultValue: "使用区里还没有图。先导入一些表情包。" }),
      action: (
        <Button disabled={!!props.disabled} onClick={() => props.onGo("import")}>
          {t("panel.overview.todo.import_go", { defaultValue: "去导入" })}
        </Button>
      ),
    });
  }

  return (
    <div className="sticker-overview">
      {note ? <Alert tone={failed ? "danger" : "info"} message={note} /> : null}
      <div className="sticker-stats">
        <div className="sticker-stat">
          <span className="sticker-stat-label">{t("panel.overview.selection", { defaultValue: "选图" })}</span>
          <span className={`sticker-stat-value is-${health.tone}`}>{health.label}</span>
          <span className="sticker-stat-sub">{health.sub}</span>
        </div>
        <div className="sticker-stat">
          <span className="sticker-stat-label">{t("fields.send_probability", { defaultValue: "配图概率" })}</span>
          <span className="sticker-stat-value">{`${probability}%`}</span>
          <span className="sticker-stat-sub">{probabilityHint}</span>
        </div>
        <div className="sticker-stat">
          <span className="sticker-stat-label">{t("panel.overview.zone", { defaultValue: "使用区" })}</span>
          <span className="sticker-stat-value">{active?.name || "—"}</span>
          <span className="sticker-stat-sub">
            {t("panel.overview.zone_sub", {
              count: activeRows.length,
              groups: activeGroups.length,
              defaultValue: "{count} 张 · {groups} 类",
            })}
          </span>
        </div>
        <div className="sticker-stat">
          <span className="sticker-stat-label">{t("panel.overview.library", { defaultValue: "全部收藏" })}</span>
          <span className="sticker-stat-value">{String(state.counts?.total ?? stickers.length)}</span>
          <span className="sticker-stat-sub">
            {t("panel.overview.library_sub", {
              enabled: state.counts?.enabled ?? 0,
              sent: state.counts?.sent_total ?? 0,
              defaultValue: "可用 {enabled} · 累计发出 {sent}",
            })}
          </span>
        </div>
      </div>

      <section className="sticker-block">
        <div className="sticker-block-head">
          <h2 className="sticker-section-title">{t("panel.overview.todo", { defaultValue: "需要处理" })}</h2>
        </div>
        {todo.length ? (
          <ul className="sticker-todo">
            {todo.map((item) => (
              <li key={item.key}>
                <span>{item.text}</span>
                {item.action || null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="sticker-muted">{t("panel.overview.todo.none", { defaultValue: "一切正常，没有要处理的事。" })}</p>
        )}
      </section>

      <section className="sticker-block">
        <div className="sticker-block-head">
          <h2 className="sticker-section-title">{t("panel.overview.recent", { defaultValue: "最近发出" })}</h2>
          <span className="sticker-muted">
            {t("panel.overview.run", {
              turns: run.turns || 0,
              attempts: attachment.attempts || 0,
              sent: run.sent || attachment.sent || 0,
              defaultValue: "本次运行：{turns} 轮 · 选图 {attempts} 次 · 发出 {sent} 张",
            })}
          </span>
        </div>
        {recent.length ? (
          <div className="sticker-recent">
            {recent.map((row) => (
              <RecentThumb key={row.id} surface={surface} row={row} onOpen={() => props.onOpenSticker(row.id)} />
            ))}
          </div>
        ) : (
          <p className="sticker-muted">{t("panel.overview.recent_none", { defaultValue: "还没有发过图。" })}</p>
        )}
      </section>
    </div>
  );
}

// 放在导出之后：旧版宿主（2026-10 Steam 版）的模块拼接器会把 JSX 闭合标签里的“/”
// 当成正则开头，排在 JSX 后面的 export 就漏剥，整个面板语法错误、显示空白。
function RecentThumb(props: { key?: string; surface: Surface; row: StickerRow; onOpen: () => void }) {
  const { preview, boxRef } = useStickerPreview(props.surface, props.row.id, false, "thumb");
  const name = props.row.desc || props.row.caption || props.row.id;
  return (
    <div className="sticker-recent-item" ref={boxRef}>
      <button
        type="button"
        aria-label={props.surface.t("panel.tile.open", { name, defaultValue: "查看：{name}" })}
        onClick={props.onOpen}
      >
        {preview ? <img src={preview} alt={name} /> : <span aria-hidden="true">…</span>}
      </button>
    </div>
  );
}
