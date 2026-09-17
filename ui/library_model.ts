// 库卡动作模型（v0.10.1 拆分自 panel.tsx）：「她的表情库」这张卡的状态与全部动作闭包。
// 本文件不放任何呈现 JSX——它是“这张卡怎么想”，panel.tsx 里只留“怎么摆”。
//
// 尺没动，成文的纪律原样继承：
// - 长任务两把尺对齐（陷阱 19）：导入/导出/收尾走 LONG_CALL，分块是短任务维持默认；
// - 收图走已测的 add 通道逐张收（魔数/查重/体积尺全复用），不再拿文件名当描述（轮 I）；
// - 全库共用**一个**隐藏图片输入框，目标分类走 ref 而不是 state（陷阱区轮 I 注释）；
// - 删分类确认摊的是服务端张数 `section.total`（陷阱 22）。

import { useConfirm, useEffect, useRef, useState } from "@neko/plugin-ui";
import {
  callAction,
  dataUrlToBase64,
  extractCode,
  readAsDataUrl,
  LONG_CALL,
  MAX_BATCH_FILES,
  MAX_STICKER_BYTES,
} from "./shared";
import type { Section, Surface, ZoneInfo } from "./shared";

function readFileChunk(blob: any): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const comma = text.indexOf(",");
      resolve(comma >= 0 ? text.slice(comma + 1) : "");
    };
    reader.onerror = () => reject(new Error("read_failed"));
    reader.readAsDataURL(blob);
  });
}

export function useLibraryModel(surface: Surface) {
  const t = surface.t;
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [libraryNote, setLibraryNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [batchTags, setBatchTags] = useState("");
  const [batchGroup, setBatchGroup] = useState("");
  // 轮 G：分类说明改为就地编辑——同一时刻只开一个区块的编辑行。
  const [descEditing, setDescEditing] = useState("");
  const [descDraft, setDescDraft] = useState("");
  // 轮 I（分类优先）：新建分类也是就地展开（同一时刻只开一个），
  // 不用覆盖层弹窗——理由见陷阱 20（kit Modal 在宿主 iframe 里平台级残废）。
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [collectBusy, setCollectBusy] = useState(false);
  // v0.9.1：详情载体是聚焦卡（库卡顶部就地展开），不是弹窗——kit Modal 在宿主里平台级残废。
  const [focus, setFocus] = useState("");
  const zipInputRef = useRef<any>(null);
  // 轮 I：全库共用**一个**隐藏图片输入框，目标分类走 ref 而不是 state——
  // “点区块头收图→click()→onChange” 三步里 onChange 的闭包可能抓到旧 state，
  // ref 赋值当场生效，不靠重渲染传参。
  const imgInputRef = useRef<any>(null);
  const collectTargetRef = useRef<string>("");
  // 收图的目标区也走 ref（与目标分类同一条闭包坑：click()→onChange 三步里 state 可能是旧的）。
  const viewZoneRef = useRef<string>("");
  // J-1：区（分类的上层）。`viewZone` 是主人正在看的 tab；它掉了（被删/未选）就回激活区。
  // 她只感知激活区；主人可以浏览任意区——但所有写入（建类/收图/导入）都落在正在看的区。
  const zonesList: ZoneInfo[] = (surface.state && surface.state.zones) || [];
  const activeZone = String((surface.state && surface.state.active_zone) || "");
  const [viewZone, setViewZone] = useState("");
  const zoneExists = (id: string) => {
    for (let i = 0; i < zonesList.length; i += 1) {
      if (zonesList[i].id === id) {
        return true;
      }
    }
    return false;
  };
  const view = zoneExists(viewZone) ? viewZone : activeZone;
  // 当场同步给 ref：收图/导入的闭包落点永远跟当前视图一致。
  viewZoneRef.current = view;
  useEffect(() => {
    if (viewZone && !zoneExists(viewZone)) {
      setViewZone("");
    }
  }, [surface.state]);

  // 区动作一把尺：五个入口形状一样（调→报→刷），只把文案差交出去。
  const zoneAction = async (
    actionId: string,
    args: Record<string, unknown>,
    doneKey: string,
    doneDefault: string,
    params?: Record<string, unknown>,
  ) => {
    setLibraryNote("");
    try {
      const result = await callAction(
        surface,
        actionId,
        args,
        actionId === "zone_remove" || actionId === "zone_restore_official" ? LONG_CALL : undefined,
      );
      const extra: Record<string, unknown> = { defaultValue: doneDefault };
      if (result) {
        // 拆区报删掉的张数，恢复官方报收进的张数——同一只 toast 位，两把尺各自取数。
        extra.count = result.removed ?? result.imported ?? 0;
      }
      if (params) {
        const keys = Object.keys(params);
        for (let i = 0; i < keys.length; i += 1) {
          extra[keys[i]] = params[keys[i]];
        }
      }
      setLibraryNote(t(doneKey, extra));
      setSelected([]);
      await surface.api.refresh();
      return true;
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
      return false;
    }
  };
  const createZone = (name: string, desc: string) =>
    zoneAction("zone_create", { zone: name, desc }, "panel.zone.created", "区「{name}」已建好", { name });
  const renameZone = (zoneId: string, name: string) =>
    zoneAction("zone_rename", { zone_id: zoneId, zone: name }, "panel.zone.renamed", "已改区名");
  const setZoneDesc = (zoneId: string, desc: string) =>
    zoneAction("zone_set_desc", { zone_id: zoneId, desc }, "panel.zone.desc_saved", "区说明已更新");
  const activateZone = (zoneId: string) =>
    zoneAction("zone_activate", { zone_id: zoneId }, "panel.zone.activated", "她的世界已切到这个区");
  const removeZone = (zoneId: string, name: string) =>
    zoneAction("zone_remove", { zone_id: zoneId }, "panel.zone.removed", "已拆区「{name}」（连带 {count} 张图）", { name });
  // J-2：官方区被拆后的补救（拍板 P3：只播一次 + 恢复按钮）——长任务，走 LONG_CALL 那把尺。
  const restoreOfficial = () =>
    zoneAction("zone_restore_official", {}, "panel.zone.restored", "官方收藏已恢复：收了 {count} 张");

  const exportPack = async () => {
    setLibraryNote("");
    try {
      const result = await callAction(surface, "export_pack", {}, LONG_CALL);
      if (result) {
        setLibraryNote(
          t("panel.export.done", {
            exported: result.exported ?? 0,
            skipped: result.skipped ?? 0,
            file: result.file ?? "",
            defaultValue: "已导出 {exported} 张（跳过 {skipped}）：{file}",
          }),
        );
      }
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const repair = async () => {
    setLibraryNote("");
    try {
      const result = await callAction(surface, "repair", {});
      if (result) {
        setLibraryNote(
          t("panel.repair.done", {
            entries: result.removed_entries ?? 0,
            files: result.purged_files ?? 0,
            hashes: result.backfilled_hashes ?? 0,
            defaultValue:
              "体检完成：清理条目 {entries}、孤儿文件 {files}、回填指纹 {hashes}",
          }),
        );
      }
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 收件箱导入的按钮已于 v0.10.3 从面板退场（主人拍板）：`import_inbox` 服务端入口与
  // `panel.inbox.done` 键照旧保留，只是这里不再有人按它。

  // 套图包直传（v0.6.0）：选择 .zip → 分块上传 → 服务端同一把尺导入。
  // 分块大小由服务端 start 回包定（与预览共用同一条 ZMQ 帧尺），面板不硬编码。
  const importZip = async (file: any) => {
    if (uploading) {
      return;
    }
    setUploading(true);
    setLibraryNote("");
    try {
      const start = await callAction(surface, "import_upload_start", {
        name: file.name,
        size: file.size,
      });
      const sid = String((start && start.session) || "");
      const chunkBytes = Number((start && start.chunk_bytes) || 3145728);
      const total = Math.max(1, Math.ceil(Number(file.size) / chunkBytes));
      let seq = 0;
      for (let offset = 0; offset < Number(file.size); offset += chunkBytes) {
        const chunk = await readFileChunk(
          file.slice(offset, offset + chunkBytes),
        );
        await callAction(surface, "import_upload_chunk", {
          session: sid,
          seq,
          data_base64: chunk,
        });
        seq += 1;
        setLibraryNote(
          t("panel.upload.progress", {
            done: seq,
            total,
            defaultValue: "上传中 {done}/{total}…",
          }),
        );
      }
      const fin = await callAction(
        surface,
        "import_upload_finish",
        { session: sid },
        LONG_CALL,
      );
      if (fin) {
        setLibraryNote(
          t("panel.upload.done", {
            imported: fin.imported ?? 0,
            duplicates: fin.duplicates ?? 0,
            rejected: fin.rejected ?? 0,
            failed: fin.failed ?? 0,
            defaultValue:
              "套图包导入完成：收进 {imported}、重复跳过 {duplicates}、坏图/超限 {rejected}、失败 {failed}",
          }),
        );
      }
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
    setUploading(false);
  };

  // 轮 G：分组说明编辑（“分类即 prompt”的一句维护入口），就地挂在区块头上。
  const saveGroupDesc = async () => {
    if (!descEditing) {
      return;
    }
    setLibraryNote("");
    try {
      await callAction(surface, "group_set_desc", {
        group: descEditing,
        desc: descDraft.trim(),
      });
      setLibraryNote(
        t("panel.group.desc_saved", { defaultValue: "分组说明已更新" }),
      );
      setDescEditing("");
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 轮 I：分类优先——先立分类（名字必填、说明可空），再往分类里收图。
  // 逐图字段（描述/梗义/标签）不在收图时问：收完点开图在聚焦卡里补。
  const createCategory = async () => {
    const name = String(newName || "").trim();
    if (!name) {
      setLibraryNote(
        t("panel.group.name_required", { defaultValue: "先给分类起个名字" }),
      );
      return;
    }
    setLibraryNote("");
    try {
      await callAction(surface, "group_create", {
        group: name,
        desc: String(newDesc || "").trim(),
      });
      setLibraryNote(
        t("panel.group.created", {
          name: name,
          defaultValue: "分类「{name}」已建好——点它块头的「收图」往里塞表情。",
        }),
      );
      setNewName("");
      setNewDesc("");
      setCreating(false);
      // 防“建完了但屏幕上看不到”：搜索词会把零张新区块筛掉（它不命中分类名），
      // 刚建的分类应当当场就在眼前。
      setQuery("");
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const cancelCreate = () => {
    setCreating(false);
    setNewName("");
    setNewDesc("");
  };

  // 删分类 = 连带删它里的图（主人拍板 1C）：确认里先把精确张数摊开。
  const removeCategory = async (section: Section) => {
    const answer = await confirm({
      title: t("panel.group.delete_title", { defaultValue: "删分类" }),
      message: section.total
        ? t("panel.group.delete_message", {
            name: section.name,
            count: section.total,
            defaultValue:
              "将拆掉分类「{name}」，连带删它里的 {count} 张图与文件，不可恢复。",
          })
        : t("panel.group.delete_message_empty", {
            name: section.name,
            defaultValue:
              "删掉空分类「{name}」？（它里面对她不可见，不会影哿发图）",
          }),
      tone: "danger",
    });
    if (!answer) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(
        surface,
        "group_remove",
        { group: section.group },
        LONG_CALL,
      );
      setLibraryNote(
        t("panel.group.delete_done", {
          name: section.name,
          count: (result && result.removed) ?? 0,
          defaultValue: "已删分类「{name}」（连带 {count} 张图）",
        }),
      );
      setSelected([]);
      // 聚焦卡不需要手动收：删掉的图不在 state.stickers 里，focusRow 自然为空。
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  // 轮 I（2A）：收图分散到区块头。目标分类走 ref 传递（见 collectTargetRef 注释）；
  // 逐张走已测的 add 通道（魔数/查重/体积尺全复用）。
  const collectInto = (group: string) => {
    collectTargetRef.current = group;
    if (imgInputRef.current) {
      imgInputRef.current.click();
    }
  };

  const importFiles = async (files: any, group: string) => {
    const list: any[] = Array.from(files || []);
    if (!list.length || collectBusy) {
      return;
    }
    const taken = list.slice(0, MAX_BATCH_FILES);
    setCollectBusy(true);
    setLibraryNote("");
    let ok = 0;
    let dup = 0;
    let big = 0;
    let fail = 0;
    for (let i = 0; i < taken.length; i += 1) {
      const file = taken[i];
      if (Number(file.size) > MAX_STICKER_BYTES) {
        big += 1;
      } else {
        const b64 = dataUrlToBase64(await readAsDataUrl(file));
        if (b64) {
          try {
            // 收图不问逐图字段：desc 交空串，目录正文按轮 F 那把尺回落到分类说明。
            // （不再拿文件名当描述：外部包的哈希名会把自已在目录里压到分类说明头上。）
            const result = await callAction(surface, "add", {
              data_base64: b64,
              desc: "",
              group: group,
              zone: viewZoneRef.current,
            });
            if (result && result.note === "sticker_added") {
              ok += 1;
            } else {
              fail += 1;
            }
          } catch (error) {
            const raw =
              error instanceof Error ? error.message : String(error ?? "");
            if (extractCode(raw) === "duplicate_image") {
              dup += 1;
            } else {
              fail += 1;
            }
          }
        } else {
          fail += 1; // 空 base64：读不出内容，计失败
        }
      }
      setLibraryNote(
        t("panel.collect.busy", {
          done: i + 1,
          total: taken.length,
          defaultValue: "收藏中 {done}/{total}…",
        }),
      );
    }
    setCollectBusy(false);
    await surface.api.refresh();
    const extra = list.length - taken.length;
    setLibraryNote(
      t("panel.collect.done", {
        ok: ok,
        dup: dup,
        big: big,
        fail: fail,
        defaultValue:
          "已收 {ok} · 重复跳过 {dup} · 超限略过 {big} · 失败 {fail}",
      }) +
        (group
          ? t("panel.collect.into", {
              name: group,
              defaultValue: "（进「{name}」）",
            })
          : "") +
        (extra > 0
          ? t("panel.batch.more", {
              extra: extra,
              defaultValue: "；本次未处理 {extra} 张",
            })
          : ""),
    );
  };

  // 隐藏输入框的落点：目标分类当场从 ref 取（轮 I 的 ref 纪律）。
  const chooseCollectedFiles = (files: any) => {
    return importFiles(files, collectTargetRef.current);
  };

  const toggleSelected = (id: string) => {
    setSelected((previous: string[]) =>
      previous.indexOf(id) >= 0
        ? previous.filter((x) => x !== id)
        : previous.concat(id),
    );
  };

  // 全选本组：没全选过→补齐；已全选→再点取消本组选择。
  const selectSection = (rows: Section["rows"]) => {
    const ids = rows.map((row) => row.id);
    setSelected((previous: string[]) => {
      const missing = ids.filter((id) => previous.indexOf(id) < 0);
      return missing.length
        ? previous.concat(missing)
        : previous.filter((id) => ids.indexOf(id) < 0);
    });
  };

  const clearSelection = () => {
    setSelected([]);
  };

  const runBatch = async (patch: Record<string, unknown>) => {
    if (!selected.length) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(surface, "batch_update", {
        ids: selected,
        ...patch,
      });
      if (result) {
        setLibraryNote(
          t("panel.batch.done_update", {
            updated: result.updated ?? 0,
            missing: (result.missing || []).length,
            defaultValue: "批量完成：改了 {updated}、不存在/失败 {missing}",
          }),
        );
      }
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  const batchDelete = async () => {
    if (!selected.length) {
      return;
    }
    // 对齐参考系统的破坏性确认：先把精确张数摊开，再问要不要删。
    const answer = await confirm({
      title: t("panel.batch.delete_title", { defaultValue: "批量删除" }),
      message: t("panel.batch.delete_message", {
        count: selected.length,
        defaultValue: "将删掉 {count} 张图和它们的记录，不可恢复。",
      }),
      tone: "danger",
    });
    if (!answer) {
      return;
    }
    setLibraryNote("");
    try {
      const result = await callAction(surface, "batch_remove", {
        ids: selected,
      });
      if (result) {
        setLibraryNote(
          t("panel.batch.delete_done", {
            removed: result.removed ?? 0,
            missing: (result.missing || []).length,
            defaultValue: "已删 {removed} 张、不存在 {missing}",
          }),
        );
      }
      setSelected([]);
      await surface.api.refresh();
    } catch (error) {
      const raw =
        error instanceof Error ? error.message : String(error ?? "failed");
      setLibraryNote(
        t("panel.toast.failed", {
          code: extractCode(raw),
          defaultValue: "操作失败：{code}",
        }),
      );
    }
  };

  return {
    query,
    setQuery,
    zonesList,
    activeZone,
    view,
    setViewZone,
    officialInfo: (surface.state && surface.state.official) || {},
    createZone,
    renameZone,
    setZoneDesc,
    activateZone,
    removeZone,
    restoreOfficial,
    libraryNote,
    uploading,
    collectBusy,
    selected,
    toggleSelected,
    selectSection,
    clearSelection,
    batchTags,
    setBatchTags,
    batchGroup,
    setBatchGroup,
    creating,
    setCreating,
    cancelCreate,
    newName,
    setNewName,
    newDesc,
    setNewDesc,
    descEditing,
    setDescEditing,
    descDraft,
    setDescDraft,
    focus,
    setFocus,
    zipInputRef,
    imgInputRef,
    exportPack,
    repair,
    importZip,
    saveGroupDesc,
    createCategory,
    removeCategory,
    collectInto,
    chooseCollectedFiles,
    runBatch,
    batchDelete,
  };
}
