// 导入页（第 2 阶段重排）：三步从上往下读一遍就会用——
// ① 放到哪里（区 + 分类，默认是图库里正在看的区）② 选择图片 ③ 导入之后（自动识图开关）。
// 导入完成后结果卡就在同一页，识图进度接在里面；导出与体检收进最下方的折叠。
import { Button, Select, Switch } from "@neko/plugin-ui";
import { useState } from "@neko/plugin-ui";
import { useFileDrop } from "../file_drop";
import { categoryOptions } from "../shared";
import type { Surface, ZoneInfo } from "../shared";
import { LabelProgress } from "./label_progress";
import type { LabelControl } from "./label_progress";

export function ImportPage(props: {
  surface: Surface;
  zones: ZoneInfo[];
  view: string;
  activeZone: string;
  disabled?: boolean;
  uploading: boolean;
  zipInputRef: any;
  labelControl: LabelControl;
  lastImport: null | { imported: number; duplicates: number; skipped: number; failed: number; queued: number };
  onSwitchZone: (zoneId: string) => void;
  onImportFiles: (files: any, group: string) => void;
  onShowLibrary: () => void;
  onShowFailed?: () => void;
  onGoSettings?: () => void;
  onExport: () => void;
  onRepair: () => void;
}) {
  const t = props.surface.t;
  const labeling = props.surface.state?.labeling;
  const labelSupported = !!labeling && String(labeling.status || "") !== "unsupported";
  const autoLabel = labelSupported && labeling?.auto_after_import !== false;
  const [group, setGroup] = useState("");
  const options = categoryOptions(props.surface, t, props.view);
  const target = options.some((option) => option.value === group) ? group : "";
  const drop = useFileDrop((files) => props.onImportFiles(files, target), !!props.disabled);
  const zoneLabel = (zone: ZoneInfo) => String(zone.name || zone.id) + (zone.id === props.activeZone
    ? t("panel.import.zone_in_use", { defaultValue: " · 猫娘正在用" })
    : "");
  const result = props.lastImport;
  const pick = () => {
    if (!props.disabled && !props.uploading) props.zipInputRef.current?.click();
  };
  return (
    <div className="sticker-import-page">
      <ol className="sticker-steps">
        <li className="sticker-step">
          <span className="sticker-step-num" aria-hidden="true">1</span>
          <div className="sticker-step-body">
            <h2 className="sticker-section-title">{t("panel.import.step_target", { defaultValue: "放到哪里" })}</h2>
            <div className="sticker-import-target">
              <label>
                <span>{t("panel.import.zone_short", { defaultValue: "区" })}</span>
                <Select
                  value={props.view}
                  disabled={props.disabled}
                  options={props.zones.map((zone) => ({ value: zone.id, label: zoneLabel(zone) }))}
                  onChange={(next: any) => props.onSwitchZone(String(next ?? ""))}
                />
              </label>
              <label>
                <span>{t("panel.import.group", { defaultValue: "分类" })}</span>
                <Select
                  value={target}
                  disabled={props.disabled}
                  options={options}
                  onChange={(next: any) => setGroup(String(next ?? ""))}
                />
              </label>
            </div>
            <p className="sticker-muted">
              {t("panel.import.target_hint", { defaultValue: "默认是你在图库里正在看的区。" })}
            </p>
          </div>
        </li>
        <li className="sticker-step">
          <span className="sticker-step-num" aria-hidden="true">2</span>
          <div className="sticker-step-body">
            <h2 className="sticker-section-title">{t("panel.import.step_pick", { defaultValue: "选择图片" })}</h2>
            <div
              role="button"
              tabIndex={props.disabled || props.uploading ? -1 : 0}
              aria-disabled={props.disabled || props.uploading ? "true" : "false"}
              className={`sticker-dropzone${drop.dragging ? " is-file-dragging" : ""}`}
              onClick={pick}
              onKeyDown={(event: any) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  pick();
                }
              }}
              {...drop.handlers}
            >
              <strong>
                {props.uploading
                  ? t("panel.upload.busy", { defaultValue: "上传中…" })
                  : t("panel.import.drop_title_click", { defaultValue: "把图片或 zip 套图包拖到这里，或点击选择" })}
              </strong>
              <span className="sticker-muted">
                {t("panel.import.drop_hint", {
                  defaultValue: "支持 PNG、JPG、GIF、WebP，一次最多 64 张；zip 套图包需单独导入。重复的图会自动跳过。",
                })}
              </span>
            </div>
            <input
              ref={props.zipInputRef}
              type="file"
              accept=".zip,application/zip,image/png,image/jpeg,image/gif,image/webp"
              multiple
              disabled={props.disabled}
              style={{ display: "none" }}
              onChange={(event: any) => {
                props.onImportFiles(event.target && event.target.files, target);
                event.target.value = "";
              }}
            />
          </div>
        </li>
        <li className="sticker-step">
          <span className="sticker-step-num" aria-hidden="true">3</span>
          <div className="sticker-step-body">
            <h2 className="sticker-section-title">{t("panel.import.step_after", { defaultValue: "导入之后" })}</h2>
            {labelSupported ? (
              <div className="sticker-step-switch">
                <Switch
                  checked={autoLabel}
                  disabled={props.disabled || !!props.labelControl.busy}
                  label={t("panel.import.auto_label", { defaultValue: "自动识图，补上梗义、标签和图中文字" })}
                  onChange={(next: boolean) => { props.labelControl.run("configure", { auto_after_import: !!next }); }}
                />
                <p className="sticker-muted">
                  {autoLabel
                    ? t("panel.import.auto_label_on", {
                        defaultValue: "用宿主的免费模型，约 4 秒一张，在后台进行，可以随时暂停；你写过的内容不会被改。",
                      })
                    : t("panel.import.auto_label_off", {
                        defaultValue: "导入的图不会自动识别。之后可以在概览里点“自动识图”。",
                      })}
                </p>
              </div>
            ) : (
              <p className="sticker-muted">
                {t("panel.import.auto_label_unsupported", {
                  defaultValue: "当前宿主版本不支持自动识图。导入后可以点开图片手动补上梗义和标签。",
                })}
              </p>
            )}
          </div>
        </li>
      </ol>
      {result ? (
        <section className="sticker-import-result" aria-live="polite">
          <div className="sticker-import-result-line">
            <strong>{t("panel.import.result", { count: result.imported, defaultValue: "已导入 {count} 张" })}</strong>
            <span className="sticker-muted">
              {t("panel.import.result_sub", {
                duplicates: result.duplicates,
                skipped: result.skipped,
                failed: result.failed,
                defaultValue: "重复跳过 {duplicates} · 超限或不支持 {skipped} · 失败 {failed}",
              })}
            </span>
            <Button disabled={props.disabled} onClick={props.onShowLibrary}>
              {t("panel.import.go_library", { defaultValue: "去图库看看" })}
            </Button>
          </div>
          <LabelProgress
            surface={props.surface}
            control={props.labelControl}
            variant="inline"
            disabled={props.disabled}
            onShowFailed={props.onShowFailed}
            onGoSettings={props.onGoSettings}
          />
        </section>
      ) : null}
      <details className="sticker-menu sticker-import-maintenance">
        <summary>{t("panel.import.maintenance_fold", { defaultValue: "备份与维护（导出套图包、体检与修复）" })}</summary>
        <div className="sticker-maintenance">
          <div>
            <Button tone="default" disabled={props.disabled} onClick={props.onExport}>
              {t("panel.export.button", { defaultValue: "导出套图包" })}
            </Button>
            <p className="sticker-muted">
              {t("panel.import.export_hint", {
                defaultValue: "把整个图库连同描述、标签、分类打成 zip，存到插件数据目录，完成后显示文件位置。",
              })}
            </p>
          </div>
          <div>
            <Button tone="default" disabled={props.disabled} onClick={props.onRepair}>
              {t("panel.repair.button", { defaultValue: "体检与修复" })}
            </Button>
            <p className="sticker-muted">
              {t("panel.import.repair_hint", {
                defaultValue: "清理丢失文件的记录和没有记录的文件，补全内容指纹。",
              })}
            </p>
          </div>
        </div>
      </details>
    </div>
  );
}
