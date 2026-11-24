// 导入页（0.9.7 面板重构）：图片和 zip 套图包都从这里进，目标区与分类在导入前选好；
// 导出套图包、体检与修复也收在这一页。图库里仍可往单个分类拖图或点“添加图片”。
import { Button, Select } from "@neko/plugin-ui";
import { useState } from "@neko/plugin-ui";
import { useFileDrop } from "../file_drop";
import { categoryOptions } from "../shared";
import type { Surface, ZoneInfo } from "../shared";

export function ImportPage(props: {
  surface: Surface;
  zones: ZoneInfo[];
  view: string;
  activeZone: string;
  disabled?: boolean;
  uploading: boolean;
  zipInputRef: any;
  onSwitchZone: (zoneId: string) => void;
  onImportFiles: (files: any, group: string) => void;
  onExport: () => void;
  onRepair: () => void;
}) {
  const t = props.surface.t;
  const [group, setGroup] = useState("");
  const options = categoryOptions(props.surface, t, props.view);
  const target = options.some((option) => option.value === group) ? group : "";
  const drop = useFileDrop((files) => props.onImportFiles(files, target), !!props.disabled);
  const zoneLabel = (zone: ZoneInfo) => String(zone.name || zone.id) + (zone.id === props.activeZone
    ? t("panel.zone.badge_active", { defaultValue: " ·使用中" })
    : "");
  return (
    <div className="sticker-import-page">
      <section
        className={`sticker-dropzone${drop.dragging ? " is-file-dragging" : ""}`}
        {...drop.handlers}
      >
        <h2 className="sticker-section-title">
          {t("panel.import.drop_title", { defaultValue: "把图片或 zip 套图包拖到这里" })}
        </h2>
        <p className="sticker-muted">
          {t("panel.import.drop_hint", {
            defaultValue: "支持 PNG、JPG、GIF、WebP，一次最多 64 张；zip 套图包需单独导入。重复的图会自动跳过。",
          })}
        </p>
        <div className="sticker-import-target">
          <label>
            <span>{t("panel.import.zone", { defaultValue: "导入到区" })}</span>
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
        <div>
          <Button
            tone="primary"
            disabled={props.disabled || props.uploading}
            onClick={() => props.zipInputRef.current?.click()}
          >
            {props.uploading
              ? t("panel.upload.busy", { defaultValue: "上传中…" })
              : t("panel.import.pick", { defaultValue: "选择文件" })}
          </Button>
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
      </section>
      <section className="sticker-block">
        <div className="sticker-block-head">
          <h2 className="sticker-section-title">
            {t("panel.import.maintenance", { defaultValue: "备份与维护" })}
          </h2>
        </div>
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
      </section>
    </div>
  );
}
