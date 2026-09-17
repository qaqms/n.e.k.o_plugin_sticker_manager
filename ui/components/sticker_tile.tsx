// 格子（v0.9.1 定稿，v0.10.1 拆分自 panel.tsx）：纯图方块。
// scale-down 只缩不放——160px 小图塞 360px 大格被放大糊脸，就是"拉伸感"的真凶
//（实机截图钉的坑）。左上角勾选框是唯一的体外控件；
// 点击=在墙上方展开聚焦卡（弹窗形态为何不可用见 DESIGN.md 陷阱 20）。

import { StatusBadge } from "@neko/plugin-ui";
import { useStickerPreview } from "../preview";
import type { StickerRow, Surface } from "../shared";

export function StickerTile(props: {
  key?: string;
  row: StickerRow;
  surface: Surface;
  selected?: boolean;
  onToggleSelect?: () => void;
  onOpen?: () => void;
}) {
  const row = props.row;
  const t = props.surface.t;
  const { preview, loading, boxRef } = useStickerPreview(
    props.surface,
    row.id,
    false,
  );

  const tileBox = (extra: Record<string, unknown>) => {
    const base: Record<string, unknown> = {
      position: "relative",
      width: "100%",
      aspectRatio: "1 / 1",
      borderRadius: 8,
      overflow: "hidden",
      cursor: "pointer",
      border: "1px solid rgba(128, 128, 128, 0.35)",
      background: "rgba(128, 128, 128, 0.10)",
    };
    return Object.assign(base, extra);
  };
  const centerBox: Record<string, unknown> = {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 12,
    opacity: 0.75,
  };

  return (
    <div
      ref={boxRef}
      style={tileBox(
        props.selected ? { border: "2px solid rgba(80, 160, 255, 0.9)" } : {},
      )}
      onClick={() => {
        if (props.onOpen) {
          props.onOpen();
        }
      }}
    >
      {preview ? (
        <img
          src={preview}
          alt={row.desc || row.id}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "scale-down",
            display: "block",
          }}
        />
      ) : null}
      {!preview && loading ? (
        <div style={centerBox}>
          {t("panel.tile.loading", { defaultValue: "加载中…" })}
        </div>
      ) : null}
      {!preview && !loading ? (
        <div style={centerBox}>
          {t("panel.tile.failed", { defaultValue: "图不可用" })}
        </div>
      ) : null}
      {row.disabled ? (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(20, 20, 20, 0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <StatusBadge
            tone="warning"
            label={t("panel.badge.disabled", { defaultValue: "已禁用" })}
          />
        </div>
      ) : null}
      <div
        style={{
          position: "absolute",
          top: 3,
          left: 3,
          background: "rgba(255, 255, 255, 0.85)",
          borderRadius: 4,
          padding: "1px 3px",
          lineHeight: 1,
        }}
        onClick={(event: any) => {
          event.stopPropagation();
          if (props.onToggleSelect) {
            props.onToggleSelect();
          }
        }}
      >
        <input type="checkbox" checked={!!props.selected} readOnly />
      </div>
    </div>
  );
}
