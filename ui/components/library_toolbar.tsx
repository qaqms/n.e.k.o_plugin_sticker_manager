// 库卡工具条（v0.10.1 拆分自 panel.tsx）：搜索、新建分类（含就地展开的创建表单）、
// 套图包直传（v0.10.3 起单入口「导入」：收件箱按钮已退场）、导出、体检
// ——以及全库共用的两个隐藏文件输入框。
// 隐藏输入框放这里是因为它们的宿主 DOM 就在这一排按钮旁边；
// 目标分类走 ref 传递的纪律在 library_model.ts（轮 I）。

import { Button, Field, Inline, Input, Stack } from "@neko/plugin-ui";
import type { Surface } from "../shared";

export function LibraryToolbar(props: {
  surface: Surface;
  query: string;
  setQuery: (next: string) => void;
  creating: boolean;
  uploading: boolean;
  collectBusy: boolean;
  disabled?: boolean;
  searchDisabled?: boolean;
  newName: string;
  setNewName: (next: string) => void;
  newDesc: string;
  setNewDesc: (next: string) => void;
  zipInputRef: any;
  imgInputRef: any;
  onToggleCreating: () => void;
  onCreate: () => void;
  onCancelCreate: () => void;
  onPickZip: () => void;
  onZipChosen: (file: any) => void;
  onImageFiles: (files: any) => void;
  onExport: () => void;
  onRepair: () => void;
}) {
  const t = props.surface.t;
  return (
    <Stack gap={10}>
      <div className="sticker-search">
        <label style={{ minWidth: 0 }}>
          <span className="sticker-sr-only">
            {t("panel.library.search", { defaultValue: "搜索表情包" })}
          </span>
          <Input
            type="search"
            value={props.query}
            disabled={props.searchDisabled}
            onChange={props.setQuery}
            placeholder={t("panel.search", { defaultValue: "按描述 / 标签 / id 过滤" })}
          />
        </label>
        {/* 轮 I：分类是第一等对象——先建分类，后面的区块头才有地方收图。 */}
        <Button
          className="sticker-new-category"
          tone="primary"
          disabled={props.disabled}
          onClick={() => {
            props.onToggleCreating();
          }}
        >
          {t("panel.group.new_button", { defaultValue: "新建分类" })}
        </Button>
        <Button
          className="sticker-import"
          tone="primary"
          disabled={props.disabled || props.uploading}
          onClick={() => {
            props.onPickZip();
          }}
        >
          {props.uploading
            ? t("panel.upload.busy", { defaultValue: "上传中…" })
            : t("panel.upload.pick", { defaultValue: "导入" })}
        </Button>
        <input
          ref={props.zipInputRef}
          type="file"
          accept=".zip,application/zip"
          disabled={props.disabled}
          style={{ display: "none" }}
          onChange={(event: any) => {
            const file =
              event.target && event.target.files && event.target.files[0];
            if (file) {
              props.onZipChosen(file);
            }
            event.target.value = "";
          }}
        />
      </div>
      <details className="sticker-menu">
        <summary
          aria-disabled={!!props.disabled}
          onClick={(event: any) => {
            if (props.disabled) event.preventDefault();
          }}
        >
          {t("panel.library.manage", { defaultValue: "图库管理" })}
        </summary>
        <div className="sticker-menu-actions">
          <Button disabled={props.disabled} onClick={props.onExport}>
            {t("panel.export.button", { defaultValue: "导出套图包" })}
          </Button>
          <Button disabled={props.disabled} onClick={props.onRepair}>
            {t("panel.repair.button", { defaultValue: "体检与修复" })}
          </Button>
        </div>
      </details>
      {props.creating ? (
        <Stack gap={6}>
          <Field
            label={t("panel.group.new_name", {
              defaultValue: "分类名字（必填）",
            })}
            required
          >
            <Input
              value={props.newName}
              disabled={props.disabled}
              onChange={props.setNewName}
              placeholder={t("panel.group.new_name_ph", {
                defaultValue: "例如：晚安与早安",
              })}
            />
          </Field>
          <Field
            label={t("panel.group.description", {
              defaultValue: "分类说明",
            })}
          >
            <Input
              value={props.newDesc}
              disabled={props.disabled}
              onChange={props.setNewDesc}
              placeholder={t("panel.group.new_desc_ph", {
                defaultValue: "例如：她困了、要睡了、或在装睡",
              })}
            />
          </Field>
          <Inline gap={6}>
            <Button
              tone="primary"
              disabled={props.disabled}
              onClick={() => {
                props.onCreate();
              }}
            >
              {t("panel.group.create_submit", { defaultValue: "创建分类" })}
            </Button>
            <Button
              tone="default"
              disabled={props.disabled}
              onClick={() => {
                props.onCancelCreate();
              }}
            >
              {t("panel.group.create_cancel", { defaultValue: "取消" })}
            </Button>
          </Inline>
        </Stack>
      ) : null}
      {/* 全库共用一个隐藏图片输入框：区块头的「收图」只改 collectTargetRef 再 click。 */}
      <input
        ref={props.imgInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        multiple
        disabled={props.disabled || props.collectBusy}
        style={{ display: "none" }}
        onChange={(event: any) => {
          props.onImageFiles(event.target && event.target.files);
          event.target.value = "";
        }}
      />
    </Stack>
  );
}
