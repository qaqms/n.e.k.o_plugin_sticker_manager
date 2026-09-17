// 区条（v0.11.0 J-1）：分类的上层「区」的 tab 脸面 + 区管理动作。
// 纪律：
// - 她只感知激活区（后端一把尺），这里的「使用中」徽章就是她的世界窗口标记；
// - 拆区是删分类的放大版：确认摊服务端张数（zone.total，陷阱 22 的对偶），
//   并且主人拍板要 **等 3 秒才能按确认**（防误删；纯前端闸，服务端不装慢）；
// - 零覆盖层弹窗（陷阱 20）：改名/说明都是就地展开的一行。
import { Button, Inline, Input, Stack, Text } from "@neko/plugin-ui";
import { useEffect, useState } from "@neko/plugin-ui";
import type { Surface, ZoneInfo } from "../shared";

export function ZoneBar(props: {
  key?: string;
  surface: Surface;
  zones: ZoneInfo[];
  view: string;
  activeZone: string;
  showRestore: boolean;
  onSwitch: (zoneId: string) => void;
  onCreate: (name: string, desc: string) => Promise<boolean>;
  onRename: (zoneId: string, name: string) => Promise<boolean>;
  onSetDesc: (zoneId: string, desc: string) => Promise<boolean>;
  onActivate: (zoneId: string) => Promise<boolean>;
  onRemove: (zoneId: string, name: string) => Promise<boolean>;
  onRestore: () => Promise<boolean>;
}) {
  const t = props.surface.t;
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [editing, setEditing] = useState(""); // "" | "rename" | "desc"
  const [armed, setArmed] = useState(false);
  const [armLeft, setArmLeft] = useState(0);
  const currentList = props.zones.filter((zone) => zone.id === props.view);
  const current = currentList.length ? currentList[0] : null;

  // 3 秒倒计时：armed 期间每秒减一；拆完/取消/换区都归零。
  useEffect(() => {
    if (!armed || armLeft <= 0) {
      return;
    }
    const timer = setInterval(() => {
      setArmLeft((previous) => previous - 1);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [armed, armLeft]);
  useEffect(() => {
    setArmed(false);
    setArmLeft(0);
    setEditing("");
  }, [props.view, props.zones.length]);

  const submitCreate = async () => {
    const name = String(nameDraft || "").trim();
    if (!name) {
      return;
    }
    const ok = await props.onCreate(name, String(descDraft || "").trim());
    if (ok) {
      setNameDraft("");
      setDescDraft("");
      setCreating(false);
    }
  };

  const submitEdit = async () => {
    if (!current) {
      return;
    }
    const value = String(nameDraft || "").trim();
    if (editing === "rename") {
      if (!value) {
        return;
      }
      const ok = await props.onRename(current.id, value);
      if (ok) {
        setEditing("");
      }
      return;
    }
    if (editing === "desc") {
      await props.onSetDesc(current.id, value);
      setEditing("");
    }
  };

  const tabLabel = (zone: ZoneInfo) => {
    const base = String(zone.name || "");
    const count = " (" + String(zone.total ?? 0) + ")";
    // J-2：官方区挂「官方」徽章（builtin 位是真身尺，改名不丢）；激活徽章语义不变。
    const tag = zone.builtin ? t("panel.zone.badge_builtin", { defaultValue: " ·官方" }) : "";
    return zone.active
      ? base + count + tag + t("panel.zone.badge_active", { defaultValue: " ·她在用" })
      : base + count + tag;
  };

  return (
    <Stack gap={6}>
      <Inline gap={6} align="center" wrap>
        {props.zones.map((zone) => (
          <Button
            key={zone.id}
            tone={zone.id === props.view ? "primary" : "default"}
            onClick={() => {
              props.onSwitch(zone.id);
            }}
          >
            {tabLabel(zone)}
          </Button>
        ))}
        <Button
          tone="default"
          onClick={() => {
            setCreating(!creating);
            setNameDraft("");
            setDescDraft("");
          }}
        >
          {t("panel.zone.new", { defaultValue: "新建区" })}
        </Button>
        {/* J-2 拍板 P3：官方区不在册且随包官方装在——tab 尾给一个补救入口（长任务，model 层已配 LONG_CALL）。 */}
        {props.showRestore ? (
          <Button
            tone="default"
            onClick={() => {
              props.onRestore();
            }}
          >
            {t("panel.zone.restore_official", { defaultValue: "恢复官方收藏" })}
          </Button>
        ) : null}
      </Inline>
      {creating ? (
        <Inline gap={6} align="center" wrap>
          <Input
            value={nameDraft}
            onChange={setNameDraft}
            placeholder={t("panel.zone.name_ph", { defaultValue: "区名字（如：官方收藏、战斗夜）" })}
          />
          <Input
            value={descDraft}
            onChange={setDescDraft}
            placeholder={t("panel.zone.desc_ph", { defaultValue: "这个区是干什么的（可留空）" })}
          />
          <Button
            tone="primary"
            disabled={!String(nameDraft || "").trim()}
            onClick={() => {
              submitCreate();
            }}
          >
            {t("panel.zone.create_submit", { defaultValue: "创建区" })}
          </Button>
        </Inline>
      ) : null}
      {current ? (
        <Inline gap={8} align="center" wrap>
          {current.id !== props.activeZone ? (
            <Button
              tone="primary"
              onClick={() => {
                props.onActivate(current.id);
              }}
            >
              {t("panel.zone.activate", { defaultValue: "让她改用这个区" })}
            </Button>
          ) : (
            <Text>{t("panel.zone.in_use", { defaultValue: "这个区她正在用" })}</Text>
          )}
          <Button
            tone="default"
            onClick={() => {
              setEditing(editing === "rename" ? "" : "rename");
              setNameDraft(current.name || "");
            }}
          >
            {t("panel.zone.rename", { defaultValue: "改区名" })}
          </Button>
          <Button
            tone="default"
            onClick={() => {
              setEditing(editing === "desc" ? "" : "desc");
              setNameDraft(current.desc || "");
            }}
          >
            {t("panel.zone.edit_desc", { defaultValue: "编辑区说明" })}
          </Button>
          {props.zones.length > 1 ? (
            <Button
              tone="danger"
              onClick={() => {
                setArmed(!armed);
                setArmLeft(3);
              }}
            >
              {t("panel.zone.remove", { defaultValue: "拆区" })}
            </Button>
          ) : null}
        </Inline>
      ) : null}
      {editing ? (
        <Inline gap={6} align="center" wrap>
          <Input
            value={nameDraft}
            onChange={setNameDraft}
            placeholder={
              editing === "rename"
                ? t("panel.zone.rename_ph", { defaultValue: "新区名" })
                : t("panel.zone.desc_ph", { defaultValue: "这个区是干什么的（可留空）" })
            }
          />
          <Button
            tone="primary"
            onClick={() => {
              submitEdit();
            }}
          >
            {t("panel.zone.save", { defaultValue: "保存" })}
          </Button>
        </Inline>
      ) : null}
      {armed && current ? (
        <Inline gap={8} align="center" wrap>
          <Text>
            {t("panel.zone.remove_message", {
              name: current.name,
              count: current.total ?? 0,
              defaultValue:
                "拆掉「{name}」会连带删它全部分类与 {count} 张图，不可恢复。防误删：等 3 秒。",
            })}
          </Text>
          <Button
            tone="danger"
            disabled={armLeft > 0}
            onClick={() => {
              setArmed(false);
              props.onRemove(current.id, current.name);
            }}
          >
            {armLeft > 0
              ? t("panel.zone.remove_wait", {
                  sec: armLeft,
                  defaultValue: "确认拆区（{sec}s）",
                })
              : t("panel.zone.remove_now", { defaultValue: "确认拆区" })}
          </Button>
          <Button
            tone="default"
            onClick={() => {
              setArmed(false);
            }}
          >
            {t("panel.zone.cancel", { defaultValue: "取消" })}
          </Button>
        </Inline>
      ) : null}
    </Stack>
  );
}
