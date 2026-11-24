// 图库工具条（0.9.7 面板重构）：搜索、选择模式开关（添加图片在各分类的标题行），
// 以及就地展开的新建分类表单。导入 zip、导出、体检已搬到“导入”页。
// 全库共用的隐藏图片输入框仍放这里；目标分类走 ref 的纪律在 library_model.ts（轮 I）。

import { Button, Field, Inline, Input, Stack } from "@neko/plugin-ui";
import type { Surface } from "../shared";

export function LibraryToolbar(props: {
  surface: Surface;
  query: string;
  setQuery: (next: string) => void;
  creating: boolean;
  collectBusy: boolean;
  disabled?: boolean;
  searchDisabled?: boolean;
  selecting: boolean;
  newName: string;
  setNewName: (next: string) => void;
  newDesc: string;
  setNewDesc: (next: string) => void;
  imgInputRef: any;
  onToggleSelecting: () => void;
  onCreate: () => void;
  onCancelCreate: () => void;
  onImageFiles: (files: any) => void;
}) {
  const t = props.surface.t;
  return (
    <div className="sticker-toolbar">
      <div className="sticker-search">
        <label className="sticker-search-field" style={{ minWidth: 0 }}>
          <span className="sticker-sr-only">
            {t("panel.library.search", { defaultValue: "搜索表情包" })}
          </span>
          <Input
            type="search"
            value={props.query}
            disabled={props.searchDisabled}
            onChange={props.setQuery}
            placeholder={t("panel.search", { defaultValue: "搜索名称、用途或标签" })}
          />
        </label>
        <Button
          tone="default"
          className={props.selecting ? "is-pressed" : ""}
          disabled={props.searchDisabled}
          onClick={props.onToggleSelecting}
        >
          {props.selecting
            ? t("panel.select.done", { defaultValue: "完成选择" })
            : t("panel.select.start", { defaultValue: "选择" })}
        </Button>
      </div>
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
      {/* 全库共用一个隐藏图片输入框：添加图片只改 collectTargetRef 再 click。 */}
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
    </div>
  );
}
