// 设置页里的“自动识图”一节：导入后要不要自动识别、每天最多识别多少张。
// 每日上限到了会自动暂停到第二天；免费额度的上限宿主没公布，用完同样第二天自动继续。
import { Button, Field, Input, Switch } from "@neko/plugin-ui";
import { useEffect, useState } from "@neko/plugin-ui";
import type { Surface } from "../shared";
import type { LabelControl } from "./label_progress";

const CAP_MIN = 1;
const CAP_MAX = 5000;

export function LabelSettings(props: { surface: Surface; control: LabelControl; disabled?: boolean }) {
  const t = props.surface.t;
  const labeling = props.surface.state?.labeling;
  const configured = Number(labeling?.daily_cap ?? 300);
  const [draft, setDraft] = useState(String(configured));
  useEffect(() => { setDraft(String(configured)); }, [configured]);
  if (!labeling || labeling.status === "unsupported") {
    return (
      <section className="sticker-settings sticker-label-settings">
        <h2 className="sticker-section-title">{t("panel.label.settings_title", { defaultValue: "自动识图" })}</h2>
        <p className="sticker-muted">
          {t("panel.label.unsupported_sub", { defaultValue: "更新 N.E.K.O 后可用；手动编辑不受影响" })}
        </p>
      </section>
    );
  }
  const value = Number(draft);
  const valid = Number.isInteger(value) && value >= CAP_MIN && value <= CAP_MAX;
  const busy = !!props.disabled || !!props.control.busy;
  return (
    <section className="sticker-settings sticker-label-settings">
      <h2 className="sticker-section-title">{t("panel.label.settings_title", { defaultValue: "自动识图" })}</h2>
      <div className="sticker-setting">
        <Switch
          checked={labeling.auto_after_import !== false}
          disabled={busy}
          label={t("panel.label.settings_auto", { defaultValue: "导入后自动识图" })}
          onChange={(next: boolean) => { props.control.run("configure", { auto_after_import: !!next }); }}
        />
        <div className="sticker-setting-control">
          <Field
            label={t("panel.label.settings_cap", { defaultValue: "每天最多识别（张）" })}
            help={t("panel.label.settings_cap_help", {
              defaultValue: "1–5000 张；到了上限自动暂停，第二天继续。一张约 700 token。",
            })}
            error={!valid ? t("panel.label.settings_cap_invalid", { defaultValue: "请输入 1–5000 之间的整数" }) : ""}
          >
            <Input
              type="number"
              value={draft}
              min={CAP_MIN}
              max={CAP_MAX}
              step={1}
              invalid={!valid}
              disabled={busy}
              onChange={setDraft}
            />
          </Field>
          <Button
            disabled={busy || !valid || value === configured}
            onClick={() => { props.control.run("configure", { daily_cap: value }); }}
          >
            {t("panel.label.settings_save", { defaultValue: "保存上限" })}
          </Button>
        </div>
        <p className="sticker-setting-current">
          {t("panel.label.today", {
            count: labeling.day_count ?? 0,
            cap: configured,
            defaultValue: "今天已用 {count} / {cap}",
          })}
        </p>
        {props.control.note ? <p className="sticker-label-note" role="status">{props.control.note}</p> : null}
      </div>
    </section>
  );
}
