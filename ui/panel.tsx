// Hosted TSX 面板：表情包管理。只从 `@neko/plugin-ui` 导入，业务逻辑全在 Python 侧。
//
// v0.10.1 拆分：本文件只留**装配骨架**（顶栏 + 三张卡的摆位），块的去向——
// - ui/shared.ts          类型 + 常量 + 纯函数（两处以上共用的尺）
// - ui/preview.ts         预览缓存 / 懒加载调度 / useStickerPreview
// - ui/library_model.ts   库卡的状态与动作闭包（无 JSX，"这张卡怎么想"）
// - ui/components/**      格子 / 聚焦卡 / 注入卡 / 批量条 / 分类区块 / 工具条
// 纪律照旧：新增 t() 键必须入 i18n（契约门递归扫 ui/ 全域）；长任务走 LONG_CALL；
// 覆盖层弹窗禁用（陷阱 20）。
//
// 契约要点（照 plugin/sdk/hosted-ui/index.d.ts 的精确签名写）：
// - 动作调用返回信封 `{plugin_id, action_id, result}`，真正的返回值在 `.result`。
// - 只能调被 @ui.action 暴露过的入口；检查器是**文本级**规则：
//   一律写完整的 `props.xxx.api` 成员访问，绝不出现裸 `api` 标识符（含参数名与别名）。
// - 缩略图懒加载：context 只带元数据，预览字节由 `preview` 动作按 id 现取，
//   模块级缓存（同一 iframe 生命周期内不重复取图；坏图记空串，不重试风暴）。
import {
  Alert,
  Button,
  Card,
  Divider,
  EmptyState,
  Inline,
  Page,
  Stack,
  StatusBadge,
  Switch,
  Text,
} from "@neko/plugin-ui";
import { AwarenessCard } from "./components/awareness_card";
import { BatchBar } from "./components/batch_bar";
import { CategorySection } from "./components/category_section";
import { FocusCard } from "./components/focus_card";
import { LibraryToolbar } from "./components/library_toolbar";
import { useLibraryModel } from "./library_model";
import { buildSections, callAction } from "./shared";
import type { Surface } from "./shared";

export default function Panel(props: Surface) {
  const state = props.state || {};
  const t = props.t;
  const lib = useLibraryModel(props);
  const stickers = state.stickers || [];
  const groups = state.groups || [];

  const term = lib.query.trim().toLowerCase();
  const sections = buildSections(stickers, groups, term, t);
  // 聚焦卡跟着最新库态走：被删/被筛掉就自动收起，不留幽灵卡。
  const focusRow = lib.focus
    ? stickers.filter((row) => row.id === lib.focus)[0] || null
    : null;

  return (
    <Page
      title={t("panel.title", { defaultValue: "表情包管理" })}
      subtitle={state.lanlan || ""}
    >
      <Stack gap={12}>
        {state.error_code ? (
          <Alert
            tone="danger"
            message={t(`panel.error.${state.error_code}`, {
              defaultValue: state.error_code,
            })}
          />
        ) : null}
        <Inline gap={16} align="center" wrap>
          <Switch
            checked={!!state.enabled}
            label={t("panel.switch", {
              defaultValue: "总开关（关闭后她不发图、看不到目录）",
            })}
            onChange={async (next: boolean) => {
              await callAction(props, "switch", { enabled: next });
              await props.api.refresh();
            }}
          />
          <Inline gap={6}>
            <StatusBadge
              tone="info"
              label={`${t("panel.stat.total", { defaultValue: "收藏" })} ${state.counts ? state.counts.total : 0}`}
            />
            <StatusBadge
              tone="success"
              label={`${t("panel.stat.sent", { defaultValue: "累计发出" })} ${state.counts ? state.counts.sent_total : 0}`}
            />
            <StatusBadge
              tone="warning"
              label={`${t("panel.stat.available", { defaultValue: "可用" })} ${state.counts ? state.counts.enabled : 0}`}
            />
          </Inline>
        </Inline>
        <Divider />
        <AwarenessCard surface={props} />
        <Card title={t("panel.card.library", { defaultValue: "管理表情包" })}>
          <Stack gap={10}>
            <LibraryToolbar
              surface={props}
              query={lib.query}
              setQuery={lib.setQuery}
              creating={lib.creating}
              uploading={lib.uploading}
              collectBusy={lib.collectBusy}
              newName={lib.newName}
              setNewName={lib.setNewName}
              newDesc={lib.newDesc}
              setNewDesc={lib.setNewDesc}
              zipInputRef={lib.zipInputRef}
              imgInputRef={lib.imgInputRef}
              onToggleCreating={() => {
                lib.setCreating(!lib.creating);
              }}
              onCreate={lib.createCategory}
              onCancelCreate={lib.cancelCreate}
              onPickZip={() => {
                if (lib.zipInputRef.current) {
                  lib.zipInputRef.current.click();
                }
              }}
              onZipChosen={(file: any) => {
                lib.importZip(file);
              }}
              onImageFiles={(files: any) => {
                lib.chooseCollectedFiles(files);
              }}
              onExport={() => {
                lib.exportPack();
              }}
              onRepair={() => {
                lib.repair();
              }}
            />
            {sections.length > 0 ? (
              // 轮 G：分类分区视图——每块「组名 · 张数 + 一句说明 + 就地操作」，
              // 一路滚下去就是她的收藏间目录。搜索时整块命中或逐图命中都支持。
              <Text>
                {t("panel.section.summary", {
                  groups: sections.length,
                  images: stickers.length,
                  defaultValue: "{groups} 个分区 · 共 {images} 张",
                })}
              </Text>
            ) : null}
            <BatchBar
              surface={props}
              selected={lib.selected}
              batchTags={lib.batchTags}
              setBatchTags={lib.setBatchTags}
              batchGroup={lib.batchGroup}
              setBatchGroup={lib.setBatchGroup}
              onRun={(patch: Record<string, unknown>) => {
                lib.runBatch(patch);
              }}
              onDelete={() => {
                lib.batchDelete();
              }}
              onClear={lib.clearSelection}
            />
            {lib.libraryNote ? <Text>{lib.libraryNote}</Text> : null}
            {focusRow ? (
              <FocusCard
                key={focusRow.id}
                surface={props}
                row={focusRow}
                onExit={() => {
                  lib.setFocus("");
                }}
              />
            ) : null}
            {sections.length === 0 ? (
              <Stack gap={8}>
                <EmptyState
                  title={
                    term
                      ? t("panel.filter.empty_title", {
                          defaultValue: "当前筛选没有命中",
                        })
                      : groups.length
                        ? t("panel.empty.title", {
                            defaultValue: "库还是空的",
                          })
                        : t("panel.cat.empty_title", {
                            defaultValue: "还没有分类",
                          })
                  }
                  description={
                    term
                      ? t("panel.filter.empty_hint", {
                          defaultValue: "换个词试试，或清空搜索框。",
                        })
                      : groups.length
                        ? t("panel.empty.hint", {
                            defaultValue:
                              "点任意分类块头的「收图进这一类」，或直接用上面的套图包导入。",
                          })
                        : t("panel.cat.empty_hint", {
                            defaultValue:
                              "先建一个分类：名字必填，再补一句“什么时候用这一组”（那句话就是她选图时看到的分类正文）。有了分类，块头才有地方收图。",
                          })
                  }
                />
                {!term && !groups.length ? (
                  <Button
                    tone="primary"
                    onClick={() => {
                      lib.setCreating(true);
                    }}
                  >
                    {t("panel.group.new_button", { defaultValue: "新建分类" })}
                  </Button>
                ) : null}
              </Stack>
            ) : (
              sections.map((section, index) => (
                <CategorySection
                  key={section.key}
                  surface={props}
                  section={section}
                  showDivider={index > 0}
                  selected={lib.selected}
                  collectBusy={lib.collectBusy}
                  descEditing={lib.descEditing}
                  descDraft={lib.descDraft}
                  onToggleSelect={lib.toggleSelected}
                  onOpen={(id: string) => {
                    lib.setFocus(lib.focus === id ? "" : id);
                  }}
                  onCollect={() => {
                    lib.collectInto(section.group);
                  }}
                  onSelectAll={() => {
                    lib.selectSection(section.rows);
                  }}
                  onToggleDescEditing={() => {
                    lib.setDescEditing(
                      lib.descEditing === section.group ? "" : section.group,
                    );
                    lib.setDescDraft(section.desc);
                  }}
                  setDescDraft={lib.setDescDraft}
                  onSaveDesc={lib.saveGroupDesc}
                  onCancelDesc={() => {
                    lib.setDescEditing("");
                  }}
                  onRemoveCategory={() => {
                    lib.removeCategory(section);
                  }}
                />
              ))
            )}
          </Stack>
        </Card>
      </Stack>
    </Page>
  );
}
