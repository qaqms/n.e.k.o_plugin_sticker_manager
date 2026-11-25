// 新建收藏区用的就地小表单：名字必填、一句说明可选，旁边说清“选图模型会参考这句话”。
// 不用覆盖层弹窗（陷阱 20：kit Modal 在宿主 iframe 里不可靠）。
import { Button, Input } from "@neko/plugin-ui";
import { useState } from "@neko/plugin-ui";
import type { Surface } from "../shared";

export function NewItemForm(props: {
  surface: Surface;
  namePlaceholder: string;
  descPlaceholder: string;
  submitLabel: string;
  busy?: boolean;
  onSubmit: (name: string, desc: string) => Promise<boolean>;
  onCancel: () => void;
}) {
  const t = props.surface.t;
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const submit = async () => {
    const clean = String(name || "").trim();
    if (!clean || props.busy) return;
    if (await props.onSubmit(clean, String(desc || "").trim())) {
      setName("");
      setDesc("");
    }
  };
  return (
    <div className="sticker-newform">
      <Input value={name} disabled={props.busy} onChange={setName} placeholder={props.namePlaceholder} />
      <Input value={desc} disabled={props.busy} onChange={setDesc} placeholder={props.descPlaceholder} />
      <p className="sticker-muted">
        {t("panel.newform.hint", { defaultValue: "说明可选；选图模型会参考这句话判断什么时候用。" })}
      </p>
      <div className="sticker-newform-actions">
        <Button disabled={props.busy} onClick={props.onCancel}>
          {t("panel.zone.cancel", { defaultValue: "取消" })}
        </Button>
        <Button tone="primary" disabled={props.busy || !String(name || "").trim()} onClick={() => { submit(); }}>
          {props.submitLabel}
        </Button>
      </div>
    </div>
  );
}
