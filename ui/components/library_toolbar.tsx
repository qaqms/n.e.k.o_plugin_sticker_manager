// 库卡工具条（v0.10.1 拆分自 panel.tsx）：搜索、新建分类（含就地展开的创建表单）、
// 套图包直传、收件箱导入、导出、体检——以及全库共用的两个隐藏文件输入框。
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
  pendingInbox: number;
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
  onImportInbox: () => void;
  onExport: () => void;
  onRepair: () => void;
}) {
  const t = props.surface.t;
  return (
    <Stack gap={10}>
      <Inline gap={8} align="center" wrap>
        <Input
          value={props.query}
          onChange={props.setQuery}
          placeholder={t("panel.search", {
            defaultValue: "按描述 / 标签 / id 过滤",
          })}
        />
        {/* 轮 I：分类是第一等对象——先建分类，后面的区块头才有地方收图。 */}
        <Button
          tone="primary"
          onClick={() => {
            props.onToggleCreating();
          }}
        >
          {t("panel.group.new_button", { defaultValue: "新建分类" })}
        </Button>
        <Button
          tone="primary"
          disabled={props.uploading}
          onClick={() => {
            props.onPickZip();
          }}
        >
          {props.uploading
            ? t("panel.upload.busy", { defaultValue: "上传中…" })
            : t("panel.upload.pick", { defaultValue: "选择套图包导入" })}
        </Button>
        <input
          ref={props.zipInputRef}
          type="file"
          accept=".zip,application/zip"
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
        <Button
          tone="primary"
          onClick={() => {
            props.onImportInbox();
          }}
        >
          {(t("panel.inbox.button", {
            defaultValue: "导入收件箱",
          }) as string) +
            (props.pendingInbox ? " (" + props.pendingInbox + ")" : "")}
        </Button>
        <Button
          tone="info"
          onClick={() => {
            props.onExport();
          }}
        >
          {t("panel.export.button", { defaultValue: "导出套图包" })}
        </Button>
        <Button
          tone="warning"
          onClick={() => {
            props.onRepair();
          }}
        >
          {t("panel.repair.button", { defaultValue: "体检与修复" })}
        </Button>
      </Inline>
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
              onChange={props.setNewName}
              placeholder={t("panel.group.new_name_ph", {
                defaultValue: "例如：晚安与早安",
              })}
            />
          </Field>
          <Field
            label={t("panel.group.new_desc", {
              defaultValue:
                "什么时候用这一组（可留空，之后在块头「编辑说明」补）",
            })}
          >
            <Input
              value={props.newDesc}
              onChange={props.setNewDesc}
              placeholder={t("panel.group.new_desc_ph", {
                defaultValue: "例如：她困了、要睡了、或在装睡",
              })}
            />
          </Field>
          <Inline gap={6}>
            <Button
              tone="primary"
              onClick={() => {
                props.onCreate();
              }}
            >
              {t("panel.group.create_submit", { defaultValue: "创建分类" })}
            </Button>
            <Button
              tone="default"
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
        disabled={props.collectBusy}
        style={{ display: "none" }}
        onChange={(event: any) => {
          props.onImageFiles(event.target && event.target.files);
          event.target.value = "";
        }}
      />
    </Stack>
  );
}
