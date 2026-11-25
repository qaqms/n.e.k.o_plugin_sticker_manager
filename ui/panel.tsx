// Hosted TSX 面板骨架（0.9.7 重构）：概览 / 图库 / 导入 / 设置 四个标签页。
// 图库是“左侧目录 + 中间网格 + 右侧详情”：宽面板里详情摆在网格旁边，
// 窄面板里详情替换图库工作区（浏览条件与选择都留在 library_model 里，不随详情卸载）。
import {
  Alert,
  Button,
  EmptyState,
  Page,
  SegmentedControl,
  Stack,
  StatusBadge,
  Switch,
  Text,
  useEffect,
  useElementSize,
  useRef,
  useState,
} from "@neko/plugin-ui";
import { AwarenessCard } from "./components/awareness_card";
import { BatchBar } from "./components/batch_bar";
import { CategoryNav, FAILED_FILTER, MISSING_FILTER } from "./components/category_nav";
import { CategorySection } from "./components/category_section";
import { FocusCard } from "./components/focus_card";
import { ImportPage } from "./components/import_page";
import { LABEL_POLL_MS, LabelProgress, useLabelControl } from "./components/label_progress";
import { LabelSettings } from "./components/label_settings";
import { LibraryToolbar } from "./components/library_toolbar";
import { OverviewPage } from "./components/overview";
import { ZoneBar } from "./components/zone_bar";
import { useLibraryModel } from "./library_model";
import { useFileDrop } from "./file_drop";
import { buildSections, callAction, extractCode, needsDescription, selectionHealth } from "./shared";
import type { Surface } from "./shared";

// 面板宽到能并排放下目录、网格和详情时，详情才摆到旁边。
const SIDE_DETAIL_MIN_WIDTH = 960;
const MIN_REAL_WIDTH = 200;

const PANEL_STYLES = `
.sticker-page { max-width:1280px; margin:0 auto; }
.sticker-panel { display:grid; gap:16px; min-width:0; }
.sticker-panel * { letter-spacing:0; }
.sticker-panel .neko-button { border-radius:6px; box-shadow:none; font-size:13px; white-space:normal; overflow-wrap:anywhere; }
.sticker-panel .neko-button[data-tone="default"] { color:var(--text); background:transparent; border-color:var(--border); }
.sticker-panel .neko-button.is-pressed { background:var(--primary); border-color:var(--primary); color:#fff; }
.sticker-panel .neko-button:hover { transform:none; box-shadow:none; }
.sticker-panel .neko-input,.sticker-panel .neko-select { max-width:100%; border-radius:6px; }
.sticker-panel .neko-field { min-width:0; }
.sticker-panel .neko-text { overflow-wrap:anywhere; }
.sticker-panel :is(button,summary,input,select):focus-visible { outline:2px solid var(--primary); outline-offset:2px; }
.sticker-sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.sticker-muted { margin:0; font-size:12px; color:var(--muted); line-height:1.6; }
.sticker-header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px 16px; }
.sticker-header-status { display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; font-size:12px; color:var(--muted); }
.sticker-nav.neko-segmented { width:100%; display:flex; gap:24px; padding:0; border:0; border-bottom:1px solid var(--border); border-radius:0; background:transparent; overflow-x:auto; }
.sticker-nav .neko-segmented-button { flex:0 0 auto; border:0; border-bottom:2px solid transparent; border-radius:0; padding:10px 2px; background:transparent; color:var(--muted); font-size:14px; }
.sticker-nav .neko-segmented-button.is-active { border-bottom-color:var(--primary); background:transparent; color:var(--text); box-shadow:none; }
.sticker-section-title { margin:0; font-size:15px; line-height:1.5; }
.sticker-sticky { position:sticky; top:0; z-index:3; display:grid; gap:8px; padding:8px 0; background:var(--bg); border-bottom:1px solid var(--border); min-width:0; }
.sticker-feedback { display:flex; align-items:center; flex-wrap:wrap; gap:8px; font-size:13px; min-width:0; }
.sticker-feedback .neko-text { max-height:72px; overflow:auto; font-size:13px; }
.sticker-select-hint { margin:0; font-size:13px; color:var(--muted); }
.sticker-block { display:grid; gap:10px; min-width:0; padding-top:14px; border-top:1px solid var(--border); }
.sticker-block-head { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px 12px; justify-content:space-between; }
/* overview */
.sticker-overview { display:grid; gap:18px; min-width:0; }
.sticker-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr)); gap:10px; }
.sticker-stat { display:grid; gap:2px; align-content:start; padding:10px 12px; border:1px solid var(--border); border-radius:8px; min-width:0; }
.sticker-stat-label { font-size:12px; color:var(--muted); }
.sticker-stat-value { font-size:20px; font-weight:600; line-height:1.4; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.sticker-stat-value.is-ok { color:var(--success); }
.sticker-stat-value.is-warn { color:var(--warning); }
.sticker-stat-value.is-off { color:var(--muted); }
.sticker-stat-sub { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
.sticker-todo { list-style:none; margin:0; padding:0; display:grid; }
.sticker-todo li { display:flex; align-items:center; flex-wrap:wrap; justify-content:space-between; gap:8px 12px; padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; line-height:1.6; }
.sticker-todo li > span { flex:1 1 260px; min-width:0; overflow-wrap:anywhere; }
.sticker-todo-actions { display:flex; flex-wrap:wrap; gap:6px; }
.sticker-recent { display:grid; grid-template-columns:repeat(auto-fill,72px); gap:8px; }
.sticker-recent-item { width:72px; height:72px; border:1px solid var(--border); border-radius:6px; overflow:hidden; background:var(--surface); }
.sticker-recent-item button { width:100%; height:100%; padding:0; border:0; background:transparent; cursor:pointer; display:flex; align-items:center; justify-content:center; color:var(--muted); }
.sticker-recent-item img { width:100%; height:100%; object-fit:scale-down; display:block; }
/* auto label */
.sticker-label-strip { display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; padding:8px 12px; border:1px solid var(--border); border-radius:8px; min-width:0; font-size:13px; }
.sticker-label-strip.is-running { border-color:var(--primary); }
.sticker-label-strip.is-paused { border-color:var(--warning); }
.sticker-label-strip.is-halt { border-color:var(--warning); background:rgba(232,162,58,.10); }
.sticker-label-strip.is-done { border-color:var(--success); background:rgba(92,199,119,.10); }
.sticker-label-title { font-weight:600; overflow-wrap:anywhere; }
.sticker-label-meta { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.sticker-label-bar { flex:1 1 140px; min-width:100px; height:8px; border-radius:999px; background:var(--surface); overflow:hidden; }
.sticker-label-bar > i { display:block; height:100%; border-radius:999px; background:var(--primary); transition:width 300ms ease; }
.sticker-label-strip.is-paused .sticker-label-bar > i,.sticker-label-card.is-paused .sticker-label-bar > i { background:var(--warning); }
.sticker-label-actions { display:flex; flex-wrap:wrap; gap:6px; margin-left:auto; }
.sticker-label-actions .neko-button { padding:4px 10px; font-size:12px; }
.sticker-label-note { margin:0; flex-basis:100%; font-size:12px; color:var(--danger); }
.sticker-label-card { display:grid; grid-template-columns:auto minmax(0,1fr); gap:14px; align-items:center; padding:14px 16px; border:1px solid var(--primary); border-radius:10px; min-width:0; }
.sticker-label-card.is-paused,.sticker-label-card.is-halt { border-color:var(--warning); }
.sticker-label-card.is-done { border-color:var(--success); }
.sticker-label-card-body { display:grid; gap:8px; min-width:0; grid-column:-2 / -1; }
.sticker-label-card .sticker-label-bar { flex:none; height:10px; }
.sticker-label-card .sticker-label-actions { margin-left:0; }
.sticker-label-now { width:76px; height:76px; border-radius:8px; border:2px solid var(--primary); overflow:hidden; display:flex; align-items:center; justify-content:center; background:var(--surface); color:var(--muted); }
.sticker-label-now img { width:100%; height:100%; object-fit:scale-down; display:block; }
.sticker-label-nums { display:flex; flex-wrap:wrap; gap:4px 16px; font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
/* library */
.sticker-library { display:grid; gap:12px; min-width:0; }
.sticker-library-body { display:grid; grid-template-columns:200px minmax(0,1fr); grid-template-areas:"nav head" "nav browse"; grid-template-rows:auto 1fr; gap:12px 16px; align-items:start; min-width:0; }
.sticker-library.has-detail .sticker-library-body { grid-template-columns:200px minmax(0,1fr) minmax(280px,340px); grid-template-areas:"nav head detail" "nav browse detail"; }
.sticker-library-body > .sticker-sidebar,.sticker-library-body > .sticker-catnav { grid-area:nav; }
.sticker-library-body > .sticker-zonehead-wrap { grid-area:head; }
.sticker-library-body > .sticker-browse { grid-area:browse; }
.sticker-library-body > .sticker-detail-col { grid-area:detail; }
.sticker-catnav { display:grid; gap:2px; align-content:start; position:sticky; top:0; max-height:100vh; overflow:auto; padding-right:4px; min-width:0; }
.sticker-nav-item { display:flex; justify-content:space-between; align-items:center; gap:8px; width:100%; padding:6px 8px; border:0; border-radius:6px; background:transparent; color:var(--text); font:inherit; font-size:13px; line-height:1.4; text-align:left; cursor:pointer; }
.sticker-nav-item:hover { background:var(--surface); }
.sticker-nav-item[aria-current="true"] { background:var(--surface); color:var(--primary); font-weight:600; }
.sticker-nav-item.is-attention .sticker-nav-name { color:var(--warning); }
.sticker-nav-item.is-danger .sticker-nav-name { color:var(--danger); }
.sticker-nav-item.is-create { color:var(--muted); }
.sticker-nav-item:disabled { opacity:.5; cursor:default; }
.sticker-nav-name { min-width:0; overflow-wrap:anywhere; }
.sticker-nav-count { flex-shrink:0; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
.sticker-browse { display:grid; gap:14px; min-width:0; align-content:start; }
.sticker-browse.is-file-dragging,.sticker-category.is-file-dragging,.sticker-dropzone.is-file-dragging { outline:2px dashed var(--primary); outline-offset:4px; background:var(--surface); }
.sticker-detail-col { position:sticky; top:0; max-height:100vh; overflow:auto; padding-left:16px; border-left:1px solid var(--border); min-width:0; }
.sticker-sidebar { display:grid; gap:2px; align-content:start; position:sticky; top:0; max-height:100vh; overflow:auto; padding-right:4px; min-width:0; }
.sticker-sidebar .sticker-catnav { position:static; max-height:none; overflow:visible; padding-right:0; }
.sticker-zonelist { display:grid; gap:2px; padding-bottom:10px; margin-bottom:6px; border-bottom:1px solid var(--border); min-width:0; }
.sticker-side-title { margin:4px 8px 2px; font-size:12px; font-weight:600; color:var(--muted); }
.sticker-side-hint { margin:0 8px 4px; font-size:11px; color:var(--muted); }
.sticker-zone-item { justify-content:flex-start; }
.sticker-zone-item .sticker-nav-name { flex:1; }
.sticker-zone-dot { flex:none; width:8px; height:8px; border-radius:50%; border:1.5px solid var(--border); }
.sticker-zone-dot.is-live { border-color:var(--success); background:var(--success); }
.sticker-newform { display:grid; gap:6px; padding:8px; margin:2px 0; border:1px solid var(--primary); border-radius:8px; min-width:0; }
.sticker-newform .neko-input { width:100%; }
.sticker-newform-actions { display:flex; justify-content:flex-end; gap:6px; }
.sticker-newform-actions .neko-button { padding:4px 10px; font-size:12px; }
.sticker-zonehead-wrap { display:grid; gap:6px; padding-bottom:10px; border-bottom:1px solid var(--border); min-width:0; }
.sticker-zonehead { display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; min-width:0; }
.sticker-zonehead-name { margin:0; font-size:18px; line-height:1.4; overflow-wrap:anywhere; min-width:0; }
.sticker-zonehead-sub { font-size:13px; color:var(--muted); }
.sticker-zonehead-desc { margin:0; font-size:13px; color:var(--muted); overflow-wrap:anywhere; }
.sticker-zone-live { padding:1px 10px; border-radius:999px; font-size:12px; font-weight:600; color:var(--success); background:rgba(92,199,119,.14); }
.sticker-zone-select { display:none; flex:1 1 200px; min-width:0; }
.sticker-zone-select .neko-select { width:100%; }
.sticker-zone-more { width:auto; margin-left:auto; }
.sticker-zone-more > summary { list-style:none; padding:2px 10px; border:1px solid var(--border); border-radius:6px; color:var(--text); font-weight:700; }
.sticker-zone-more > summary::-webkit-details-marker { display:none; }
.sticker-zone-more[open] { width:100%; }
.sticker-zone-more[open] > summary { width:fit-content; margin-left:auto; }
.sticker-toolbar { display:grid; gap:10px; min-width:0; }
.sticker-search { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:8px; }
.sticker-search > * { min-width:0; }
.sticker-category { display:grid; gap:8px; min-width:0; }
.sticker-category + .sticker-category { border-top:1px solid var(--border); padding-top:16px; }
.sticker-category-header { display:flex; flex-wrap:wrap; align-items:center; gap:8px; min-width:0; }
.sticker-category-name { flex:1 1 160px; min-width:0; margin:0; font-size:15px; line-height:1.5; overflow-wrap:anywhere; display:flex; align-items:baseline; gap:8px; }
.sticker-category-count { font-size:12px; font-weight:400; color:var(--muted); font-variant-numeric:tabular-nums; }
.sticker-category-actions { display:flex; flex-wrap:wrap; gap:6px; }
.sticker-category-actions .neko-button { padding:5px 10px; font-size:12px; }
.sticker-category-desc { margin:0; font-size:13px; line-height:1.6; color:var(--muted); padding-left:8px; border-left:2px solid var(--border); overflow-wrap:anywhere; }
.sticker-category-desc.is-empty { font-style:italic; }
.sticker-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,112px),1fr)); gap:8px; min-width:0; }
.sticker-grid > * { width:100%; max-width:176px; min-width:0; }
.sticker-tile { transition:border-color 120ms ease,background 120ms ease; }
.sticker-tile:hover { border-color:var(--primary) !important; }
.sticker-field-mark { padding:0 6px; border-radius:4px; font-size:11px; font-weight:600; line-height:18px; background:var(--surface); color:var(--muted); }
.sticker-field-mark.is-auto { background:rgba(122,90,245,.16); color:#7a5af5; }
.sticker-suggest { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:8px 12px; border-radius:8px; background:rgba(122,90,245,.10); font-size:13px; min-width:0; }
.sticker-suggest > span { flex:1 1 160px; min-width:0; overflow-wrap:anywhere; }
.sticker-suggest .neko-button { padding:4px 10px; font-size:12px; }
.sticker-tile-label { position:absolute; top:4px; left:4px; z-index:1; padding:0 5px; border-radius:4px; font-size:11px; font-weight:600; line-height:18px; pointer-events:none; }
.sticker-tile-label.is-queued { background:var(--bg); color:var(--muted); border:1px solid var(--border); }
.sticker-tile-label.is-working { background:var(--primary); color:#fff; }
.sticker-tile-label.is-failed { background:var(--danger); color:#fff; }
.sticker-tile-label.is-auto { background:rgba(122,90,245,.16); color:#7a5af5; }
.sticker-menu { width:100%; min-width:0; }
.sticker-menu > summary { width:fit-content; padding:6px 2px; color:var(--muted); cursor:pointer; font-size:13px; }
.sticker-menu[open] > summary { color:var(--text); }
.sticker-menu-body { display:grid; gap:10px; padding:8px 0 0; min-width:0; }
.sticker-menu-actions { display:flex; flex-wrap:wrap; gap:8px; }
.sticker-collection-count { margin:0; font-size:12px; color:var(--muted); line-height:1.6; }
.sticker-batch { display:grid; gap:6px; min-width:0; }
.sticker-batch-main { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; }
.sticker-batch-move { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:6px; min-width:0; align-items:center; }
.sticker-batch .neko-button { padding:7px 10px; font-size:12px; }
.sticker-batch-count { margin:0; font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
.sticker-batch-more { max-height:min(220px,40vh); overflow:auto; padding:8px 0 0; }
.sticker-batch .sticker-menu > summary { padding:4px 2px; }
/* import */
.sticker-import-page { display:grid; gap:16px; max-width:760px; min-width:0; }
.sticker-steps { list-style:none; margin:0; padding:0; display:grid; gap:12px; min-width:0; }
.sticker-step { display:grid; grid-template-columns:28px minmax(0,1fr); gap:12px; padding:14px 16px; border:1px solid var(--border); border-radius:10px; min-width:0; }
.sticker-step-num { width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:13px; color:var(--primary); background:var(--surface); }
.sticker-step-body { display:grid; gap:8px; min-width:0; }
.sticker-step-switch { display:grid; gap:4px; }
.sticker-dropzone { display:grid; gap:4px; justify-items:center; text-align:center; padding:22px 16px; border:1.5px dashed var(--primary); border-radius:10px; background:var(--surface); color:var(--primary); cursor:pointer; min-width:0; }
.sticker-dropzone[aria-disabled="true"] { opacity:.6; cursor:default; }
.sticker-dropzone .sticker-muted { color:var(--muted); }
.sticker-import-result { display:grid; gap:10px; padding:14px 16px; border:1px solid var(--success); border-radius:10px; min-width:0; }
.sticker-import-result-line { display:flex; flex-wrap:wrap; align-items:center; gap:6px 14px; min-width:0; }
.sticker-import-result-line .neko-button { margin-left:auto; padding:4px 10px; font-size:12px; }
.sticker-import-maintenance { padding-top:10px; border-top:1px solid var(--border); }
.sticker-import-target { display:flex; flex-wrap:wrap; gap:10px 16px; width:100%; }
.sticker-import-target label { display:flex; align-items:center; gap:8px; flex:1 1 220px; min-width:0; font-size:13px; }
.sticker-import-target label > span { flex-shrink:0; color:var(--muted); }
.sticker-import-target .neko-select { flex:1; min-width:0; }
.sticker-maintenance { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr)); gap:14px; }
.sticker-maintenance > div { display:grid; gap:6px; justify-items:start; min-width:0; }
/* settings */
.sticker-settings { display:grid; gap:24px; max-width:760px; min-width:0; }
.sticker-preferences { display:grid; gap:24px; min-width:0; }
.sticker-setting { display:grid; gap:12px; min-width:0; }
.sticker-setting + .sticker-setting { border-top:1px solid var(--border); padding-top:24px; }
.sticker-setting .neko-segmented { display:flex; width:100%; border-radius:6px; }
.sticker-setting .neko-segmented-button { flex:1; min-width:0; border-radius:4px; font-size:13px; white-space:normal; }
.sticker-setting-control { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:end; max-width:400px; }
.sticker-setting-control .neko-field-label { line-height:20px; }
.sticker-setting-control > .neko-button { align-self:start; margin-top:26px; }
.sticker-setting-current { margin:0; font-size:12px; color:var(--muted); }
.sticker-advanced { padding-top:16px; border-top:1px solid var(--border); }
.sticker-advanced > summary { font-weight:600; color:var(--text); }
.sticker-advanced > .sticker-menu-body { gap:20px; padding-top:16px; }
.sticker-diagnostics { display:grid; gap:16px; min-width:0; }
.sticker-readouts { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr)); gap:12px; margin:0; min-width:0; }
.sticker-readouts > div { display:grid; gap:4px; min-width:0; padding:8px 0; border-bottom:1px solid var(--border); }
.sticker-readouts dt { font-size:12px; color:var(--muted); }
.sticker-readouts dd { margin:0; font-size:14px; line-height:1.5; overflow-wrap:anywhere; }
@media (max-width:720px) {
  .sticker-library-body,.sticker-library.has-detail .sticker-library-body { grid-template-columns:minmax(0,1fr); grid-template-areas:"head" "nav" "browse" "detail"; grid-template-rows:auto; }
  .sticker-sidebar { position:static; max-height:none; overflow:visible; padding-right:0; grid-template-columns:minmax(0,1fr); }
  .sticker-sidebar .sticker-catnav { overflow-x:auto; }
  .sticker-zonelist,.sticker-side-title { display:none; }
  .sticker-zone-select { display:block; }
  .sticker-zonehead-name { display:none; }
  .sticker-catnav { position:static; max-height:none; display:flex; gap:6px; overflow-x:auto; padding:0 0 4px; }
  .sticker-nav-item { width:auto; flex:0 0 auto; white-space:nowrap; border:1px solid var(--border); border-radius:999px; padding:4px 10px; }
  .sticker-nav-name { overflow-wrap:normal; }
}
@media (max-width:480px) {
  .sticker-page.neko-page { padding:12px; }
  .sticker-panel .neko-button { padding:7px 10px; font-size:13px; }
  .sticker-batch-main { grid-template-columns:1fr auto; gap:6px; }
  .sticker-grid { grid-template-columns:repeat(auto-fill,minmax(min(100%,88px),1fr)); }
  .sticker-dropzone { padding:14px; }
}
`;

const TABS = ["overview", "library", "import", "settings"];
const TAB_LABELS: Record<string, string> = { overview: "概览", library: "图库", import: "导入", settings: "设置" };

export default function Panel(props: Surface) {
  const state = props.state || {};
  const t = props.t;
  const lib = useLibraryModel(props);
  const labelControl = useLabelControl(props, {
    onRefreshFailed: lib.reportRefreshFailure,
    onRefreshRecovered: lib.clearRefreshFailure,
  });
  const labelStatus = String(state.labeling?.status || "");
  const panelRef = useRef<any>(null);
  const size = useElementSize(panelRef);
  // A hidden iframe (or a full-page capture) briefly reports a zero or tiny width
  // (26px seen in Chrome); keep the last real width so an open side detail does not
  // jump to the page layout. No real host panel is narrower than MIN_REAL_WIDTH.
  const lastWidthRef = useRef(0);
  if (Number(size?.width || 0) >= MIN_REAL_WIDTH) lastWidthRef.current = Number(size.width);
  const wide = lastWidthRef.current >= SIDE_DETAIL_MIN_WIDTH;
  const [viewTab, setViewTab] = useState("overview");
  const [categoryFilter, setCategoryFilter] = useState("");
  const pendingFilterRef = useRef<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchNote, setSwitchNote] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const switchBusyRef = useRef(false);
  const operationBusy = !!lib.pending || switchBusy || settingsBusy;
  const managementBusy = operationBusy || !!lib.refreshFailed;
  const groupOf = (filter: string) => (filter.indexOf("group:") === 0 ? filter.slice(6) : "");
  const drop = useFileDrop(
    (files) => { lib.importDroppedFiles(files, groupOf(categoryFilter)); },
    managementBusy,
  );
  const stickers = state.stickers || [];
  const groups = state.groups || [];
  const zones = lib.zonesList;
  const view = lib.view;
  const term = lib.query.trim().toLowerCase();
  const sectionValue = (group: string) => `group:${group}`;
  const allSections = buildSections(stickers, groups, "", t, view);
  const matchingSections = buildSections(stickers, groups, term, t, view);
  const missingRows = (rows: typeof stickers) => rows.filter((row) => !row.disabled && needsDescription(row));
  const failedRows = (rows: typeof stickers) => rows.filter((row) => row.label_state === "failed");
  const sections = categoryFilter === MISSING_FILTER || categoryFilter === FAILED_FILTER
    ? matchingSections
        .map((section) => ({
          ...section,
          rows: categoryFilter === MISSING_FILTER ? missingRows(section.rows) : failedRows(section.rows),
        }))
        .filter((section) => section.rows.length)
    : categoryFilter
      ? matchingSections.filter((section) => sectionValue(section.group) === categoryFilter)
      : matchingSections;
  const visibleIds = sections.flatMap((section) => section.rows.map((row) => row.id));
  const matchingIds = matchingSections.flatMap((section) => section.rows.map((row) => row.id));
  const hiddenSelected = lib.selected.filter((id) => visibleIds.indexOf(id) < 0).length;
  const zoneRows = stickers.filter((row) => String(row.zone || "") === view);
  const zoneTotal = zoneRows.length;
  const missingTotal = missingRows(zoneRows).length;
  const failedTotal = failedRows(zoneRows).length;
  const activeZone = zones.filter((zone) => zone.id === lib.activeZone)[0];
  const health = selectionHealth(state, t);
  const focusRow = lib.focus
    ? stickers.filter((row) => row.id === lib.focus && matchingIds.indexOf(row.id) >= 0)[0] || null
    : null;
  const sideDetail = !!focusRow && wide && viewTab === "library";

  useEffect(() => {
    const next = pendingFilterRef.current;
    pendingFilterRef.current = null;
    setCategoryFilter(next || "");
  }, [view]);
  useEffect(() => {
    if (categoryFilter.indexOf("group:") === 0 &&
        !allSections.some((section) => sectionValue(section.group) === categoryFilter)) {
      setCategoryFilter("");
    }
  }, [categoryFilter, view, state.groups, state.stickers]);
  useEffect(() => {
    if (lib.focus && !focusRow) lib.closeFocus();
  }, [lib.focus, focusRow]);
  // 识图进行中每 2 秒刷新一次进度；面板藏起来或别的操作在跑时跳过这一拍，空闲时不轮询。
  useEffect(() => {
    if (labelStatus !== "running") return;
    const timer = setInterval(() => {
      if (document.hidden || lib.pending || labelControl.busy) return;
      props.api.refresh().catch(() => {});
    }, LABEL_POLL_MS);
    return () => clearInterval(timer);
  }, [labelStatus, lib.pending, labelControl.busy]);

  const changeEnabled = async (next: boolean) => {
    if (switchBusyRef.current || managementBusy) return;
    switchBusyRef.current = true;
    setSwitchBusy(true);
    setSwitchNote("");
    let saved = false;
    try {
      await callAction(props, "switch", { enabled: next });
      saved = true;
      await props.api.refresh();
      lib.clearRefreshFailure();
    } catch (error) {
      if (saved) {
        lib.reportRefreshFailure();
      } else {
        const raw = error instanceof Error ? error.message : String(error ?? "failed");
        const code = extractCode(raw);
        setSwitchNote(t(`panel.error.${code}`, { defaultValue: code }));
      }
    } finally {
      switchBusyRef.current = false;
      setSwitchBusy(false);
    }
  };
  const openSticker = (id: string) => {
    if (selecting) return;
    lib.openFocus(id, wide ? "side" : "page");
  };
  const goToFilter = (zoneId: string, filter: string) => {
    setViewTab("library");
    if (zoneId && zoneId !== view) {
      pendingFilterRef.current = filter;
      lib.setViewZone(zoneId);
    } else {
      setCategoryFilter(filter);
    }
  };
  const showFailed = () => {
    const failed = stickers.filter((row) => row.label_state === "failed");
    const inView = failed.some((row) => String(row.zone || "") === view);
    goToFilter(inView || !failed.length ? view : String(failed[0].zone || ""), FAILED_FILTER);
  };
  const openFromOverview = (id: string) => {
    const row = stickers.filter((item) => item.id === id)[0];
    if (!row) return;
    setSelecting(false);
    setViewTab("library");
    setCategoryFilter("");
    lib.setQuery("");
    if (String(row.zone || "") !== view) lib.setViewZone(String(row.zone || ""));
    lib.openFocus(id, wide ? "side" : "page");
  };
  const toggleSelecting = () => {
    if (selecting) {
      setSelecting(false);
      lib.clearSelection();
    } else {
      if (lib.focus) lib.closeFocus();
      setSelecting(true);
    }
  };

  if (focusRow && !sideDetail && viewTab === "library") {
    return (
      <Page className="sticker-page" title={t("panel.focus.title", { defaultValue: "表情详情" })}>
        <style>{PANEL_STYLES}</style>
        <FocusCard
          key={focusRow.id}
          surface={props}
          row={focusRow}
          layout="page"
          onExit={lib.closeFocus}
          onRefreshFailed={lib.reportRefreshFailure}
          onRefreshRecovered={lib.clearRefreshFailure}
        />
      </Page>
    );
  }

  const showSticky = (viewTab === "library" && (lib.selected.length || selecting)) ||
    !!lib.pending || !!lib.libraryNote || !!lib.refreshFailed;

  return (
    // 不传 title：宿主页头已写“表情包管理”；角色名并进下方开关，不再独占一行。
    <Page className="sticker-page">
      <style>{PANEL_STYLES}</style>
      <div className="sticker-panel" ref={panelRef}>
        {state.error_code ? (
          <Alert tone="danger" message={t(`panel.error.${state.error_code}`, { defaultValue: state.error_code })} />
        ) : null}
        {switchNote ? <Alert tone="danger" message={switchNote} /> : null}
        <div className="sticker-header">
          <Switch
            checked={!!state.enabled}
            disabled={managementBusy}
            label={state.lanlan
              ? t("panel.switch.named", { name: state.lanlan, defaultValue: "{name} 发表情包" })
              : t("panel.switch.compact", { defaultValue: "猫娘发表情包" })}
            onChange={changeEnabled}
          />
          <div className="sticker-header-status">
            <StatusBadge
              tone={health.tone === "ok" ? "success" : health.tone === "warn" ? "warning" : "default"}
              label={`${t("panel.overview.selection", { defaultValue: "选图" })} · ${health.label}`}
            />
            <span>
              {t("panel.zone.current", {
                name: activeZone?.name || "—",
                defaultValue: "使用区：{name}",
              })}
            </span>
          </div>
        </div>
        <SegmentedControl
          className="sticker-nav"
          value={viewTab}
          disabled={operationBusy}
          options={TABS.map((value) => ({
            value,
            label: t(`panel.view.${value}`, {
              defaultValue: TAB_LABELS[value] || value,
            }),
          }))}
          onChange={(next: any) => setViewTab(String(next))}
        />
        {viewTab !== "overview" && !(viewTab === "import" && lib.lastImport) ? (
          <LabelProgress
            surface={props}
            control={labelControl}
            variant="strip"
            disabled={managementBusy}
            onShowFailed={showFailed}
            onGoSettings={() => setViewTab("settings")}
          />
        ) : null}
        {showSticky ? (
          <div className="sticker-sticky">
            {viewTab === "library" && lib.selected.length ? (
              <BatchBar
                surface={props}
                selected={lib.selected}
                hiddenCount={hiddenSelected}
                zone={view}
                disabled={managementBusy}
                batchTags={lib.batchTags}
                setBatchTags={lib.setBatchTags}
                batchGroup={lib.batchGroup}
                setBatchGroup={lib.setBatchGroup}
                onRun={(patch) => { lib.runBatch(patch); }}
                onDelete={() => { lib.batchDelete(hiddenSelected); }}
                onClear={lib.clearSelection}
                onClearHidden={() => lib.clearHiddenSelection(visibleIds)}
              />
            ) : viewTab === "library" && selecting ? (
              <p className="sticker-select-hint">
                {t("panel.select.hint", { defaultValue: "点图片勾选，可批量移动、加删标签、停用或删除。" })}
              </p>
            ) : null}
            {lib.libraryNote || lib.pending || lib.refreshFailed ? (
              <div className="sticker-feedback" role="status" aria-live="polite">
                <Text>
                  {lib.libraryNote || t("panel.action.pending", { defaultValue: "处理中…" })}
                </Text>
                {lib.refreshFailed ? (
                  <Button disabled={operationBusy} onClick={() => { lib.retryRefresh(); }}>
                    {t("panel.refresh.retry", { defaultValue: "重试刷新" })}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {viewTab === "overview" ? (
          <OverviewPage
            surface={props}
            labelControl={labelControl}
            onShowFailed={showFailed}
            onGoSettings={() => setViewTab("settings")}
            disabled={managementBusy}
            onOpenSticker={openFromOverview}
            onShowMissing={() => goToFilter(lib.activeZone, MISSING_FILTER)}
            onGo={(tab) => setViewTab(tab)}
            onRefreshFailed={lib.reportRefreshFailure}
            onRefreshRecovered={lib.clearRefreshFailure}
          />
        ) : null}
        {viewTab === "library" ? (
          <div className={`sticker-library${sideDetail ? " has-detail" : ""}`}>
            <div className="sticker-library-body">
              <ZoneBar
                surface={props}
                zones={zones}
                view={view}
                activeZone={lib.activeZone}
                categories={allSections.filter((section) => section.editable).length}
                pending={managementBusy ? lib.pending || "external" : ""}
                showRestore={!!(lib.officialInfo?.pack && !lib.officialInfo.zone)}
                onSwitch={lib.setViewZone}
                onCreate={lib.createZone}
                onRename={lib.renameZone}
                onSetDesc={lib.setZoneDesc}
                onActivate={lib.activateZone}
                onRemove={lib.removeZone}
                onRestore={lib.restoreOfficial}
              />
              <CategoryNav
                surface={props}
                sections={allSections}
                total={zoneTotal}
                missing={missingTotal}
                failed={failedTotal}
                value={categoryFilter}
                disabled={managementBusy}
                zones={zones}
                view={view}
                activeZone={lib.activeZone}
                onSwitchZone={lib.setViewZone}
                onCreateZone={lib.createZone}
                onChange={(next) => setCategoryFilter(next)}
                onCreate={() => { lib.setCreating(true); }}
              />
              <div className={`sticker-browse${drop.dragging ? " is-file-dragging" : ""}`} {...drop.handlers}>
                <LibraryToolbar
                  surface={props}
                  query={lib.query}
                  setQuery={lib.setQuery}
                  creating={lib.creating}
                  collectBusy={lib.collectBusy}
                  disabled={managementBusy}
                  searchDisabled={operationBusy}
                  selecting={selecting}
                  newName={lib.newName}
                  setNewName={lib.setNewName}
                  newDesc={lib.newDesc}
                  setNewDesc={lib.setNewDesc}
                  imgInputRef={lib.imgInputRef}
                  onToggleSelecting={toggleSelecting}
                  onCreate={lib.createCategory}
                  onCancelCreate={lib.cancelCreate}
                  onImageFiles={(files) => { lib.chooseCollectedFiles(files); }}
                />
                <p className="sticker-collection-count">
                  {categoryFilter === FAILED_FILTER
                    ? t("panel.library.failed_hint", {
                        count: visibleIds.length,
                        defaultValue: "{count} 张图自动识图没成功。可以点上方的“重试失败的”，或点开一张手动补。",
                      })
                    : categoryFilter === MISSING_FILTER
                    ? t("panel.library.missing_hint", {
                        count: visibleIds.length,
                        defaultValue: "{count} 张图没有梗义和标签。点开一张，在详情里补上它在聊天中表达什么。",
                      })
                    : t("panel.library.visible", {
                        visible: visibleIds.length,
                        total: zoneTotal,
                        categories: sections.length,
                        defaultValue: "显示 {visible} / {total} 张 · {categories} 个分类",
                      })}
                </p>
                {sections.length === 0 ? (
                  <Stack gap={8}>
                    <EmptyState
                      title={term || categoryFilter
                        ? t("panel.filter.empty_title", { defaultValue: "当前筛选没有命中" })
                        : t("panel.cat.empty_title", { defaultValue: "还没有分类" })}
                    />
                    {!term && !categoryFilter && !allSections.length ? (
                      <Button disabled={managementBusy} onClick={() => { lib.setCreating(true); }}>
                        {t("panel.group.new_button", { defaultValue: "新建分类" })}
                      </Button>
                    ) : null}
                  </Stack>
                ) : sections.map((section) => (
                  <CategorySection
                    key={section.key}
                    surface={props}
                    section={section}
                    selected={lib.selected}
                    selecting={selecting}
                    activeId={sideDetail ? lib.focus : ""}
                    collectBusy={lib.collectBusy}
                    disabled={managementBusy}
                    descEditing={lib.descEditing}
                    descDraft={lib.descDraft}
                    onToggleSelect={lib.toggleSelected}
                    onOpen={openSticker}
                    onCollect={() => lib.collectInto(section.group)}
                    onDropFiles={(files) => { lib.importDroppedFiles(files, section.group); }}
                    onSelectAll={() => lib.selectSection(section.rows)}
                    onToggleDescEditing={() => {
                      lib.setDescEditing(lib.descEditing === section.group ? "" : section.group);
                      lib.setDescDraft(section.desc);
                    }}
                    setDescDraft={lib.setDescDraft}
                    onSaveDesc={lib.saveGroupDesc}
                    onCancelDesc={() => lib.setDescEditing("")}
                    onRemoveCategory={() => { lib.removeCategory(section); }}
                  />
                ))}
              </div>
              {sideDetail && focusRow ? (
                <aside className="sticker-detail-col">
                  <FocusCard
                    key={focusRow.id}
                    surface={props}
                    row={focusRow}
                    layout="side"
                    onExit={lib.closeFocus}
                    onRefreshFailed={lib.reportRefreshFailure}
                    onRefreshRecovered={lib.clearRefreshFailure}
                  />
                </aside>
              ) : null}
            </div>
          </div>
        ) : null}
        {viewTab === "import" ? (
          <ImportPage
            surface={props}
            labelControl={labelControl}
            lastImport={lib.lastImport}
            onShowLibrary={() => { setCategoryFilter(""); setViewTab("library"); }}
            onShowFailed={showFailed}
            onGoSettings={() => setViewTab("settings")}
            zones={zones}
            view={view}
            activeZone={lib.activeZone}
            disabled={managementBusy}
            uploading={lib.uploading}
            zipInputRef={lib.zipInputRef}
            onSwitchZone={lib.setViewZone}
            onImportFiles={(files, group) => { lib.importDroppedFiles(files, group); }}
            onExport={() => { lib.exportPack(); }}
            onRepair={() => { lib.repair(); }}
          />
        ) : null}
        {viewTab === "settings" ? (
          <AwarenessCard
            surface={props}
            mode="settings"
            disabled={!!lib.pending || switchBusy || lib.refreshFailed}
            onBusyChange={setSettingsBusy}
            onRefreshFailed={lib.reportRefreshFailure}
            onRefreshRecovered={lib.clearRefreshFailure}
          />
        ) : null}
        {viewTab === "settings" ? (
          <LabelSettings surface={props} control={labelControl} disabled={managementBusy} />
        ) : null}
      </div>
    </Page>
  );
}
