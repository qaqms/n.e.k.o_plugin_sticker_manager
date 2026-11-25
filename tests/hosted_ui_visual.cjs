// Offline preview with the actual host renderer and bundled images. No live host API.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createRequire } = require("node:module");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "..");
const arg = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
};
const host = path.resolve(arg("--host-root", path.join(root, "../N.E.K.O")));
const output = path.resolve(arg("--out", path.join(root, ".tmpgate/ui-preview")));
const modules = path.resolve(arg("--node-modules", path.join(host, "frontend/plugin-manager/node_modules")));
const tsModules = path.resolve(arg("--typescript-modules", modules));
const python = arg("--python", path.join(root, ".venv/Scripts/python.exe"));
const ts = createRequire(path.join(tsModules, "__visual__.cjs"))("typescript");
const { chromium } = createRequire(path.join(modules, "__visual__.cjs"))("playwright");
const kit = path.join(host, "frontend/plugin-manager/src/components/plugin/hosted/ui-kit");
const fixture = JSON.parse(execFileSync(python, ["-c", `
import base64, importlib.util, io, json
from pathlib import Path
from PIL import Image
spec = importlib.util.spec_from_file_location("bundled", "services/bundled_pack.py")
bundled = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundled)
rows, images, groups = [], {}, []
with bundled.open_pack(Path("official/official_pack.zip")) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    for group in manifest["groups"]:
        selected = [row for row in manifest["stickers"] if row["group"] == group["name"]][:8]
        if group["name"] not in ["开心与庆祝", "比心与撒娇", "打招呼与冒泡"]:
            continue
        groups.append({**group, "zone": "z1", "count": len(selected)})
        for row in selected:
            image = Image.open(io.BytesIO(archive.read("stickers/" + row["file"]))).convert("RGBA")
            image.thumbnail((256, 256))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            sticker_id = row["asset_id"]
            images[sticker_id] = base64.b64encode(buffer.getvalue()).decode("ascii")
            rows.append({**row, "id": sticker_id, "zone": "z1"})
print(json.dumps({"images": images, "state": {
    "enabled": True, "lanlan": "YUI", "eagerness": "natural", "active_zone": "z1",
    "zones": [{"id": "z1", "name": "官方收藏", "active": True, "builtin": True, "total": len(rows)},
              {"id": "z2", "name": "我的收藏", "total": 0}],
    "stickers": rows, "groups": groups,
    "counts": {"total": len(rows), "enabled": len(rows), "groups": len(groups), "sent_total": 12},
    "config": {"cooldown_sec": 0, "probability": 0.5},
    "reply_completed": {"mode": "hook", "status": "subscribed", "model_result": {"duration_ms": 1840}},
    "eager_attachment": {"status": "registered", "attempts": 9, "sent": 4},
    "usage": [{"id": row["id"], "ok": True} for row in rows[:6]],
    "awareness": {"status": "waiting", "target": "YUI", "min_interval_sec": 60,
                  "inject_interval_n": 6, "turns_since": 2, "driver": "bus"},
    "run": {"turns": 29, "tool_calls": 0, "sent": 4, "refused": 0, "surface_categories": 3}
}}, ensure_ascii=False))
`], { cwd: root, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONIOENCODING: "utf-8" },
  encoding: "utf8", maxBuffer: 16 * 1024 * 1024 }));

// One sticker the owner imported without describing it: the overview and the
// library's "needs description" filter should both surface it.
Object.assign(fixture.state.stickers[fixture.state.stickers.length - 1],
  { desc: "IMG_0412", caption: "", tags: [], visible_text: "" });

const compiled = {};
function collect(relative) {
  const filename = path.resolve(root, relative);
  const key = path.relative(root, filename).replaceAll("\\", "/");
  if (compiled[key]) return;
  const source = fs.readFileSync(filename, "utf8");
  compiled[key] = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React, jsxFactory: "h", jsxFragmentFactory: "Fragment" },
  }).outputText;
  for (const match of source.matchAll(/from\s+["'](\.[^"']+)["']/g)) {
    const base = path.resolve(path.dirname(filename), match[1]);
    collect(fs.existsSync(`${base}.tsx`) ? `${base}.tsx` : `${base}.ts`);
  }
}
collect("ui/panel.tsx");
const factories = Object.entries(compiled).map(([key, value]) =>
  `${JSON.stringify(key)}: function(require,module,exports) {${value}\n}`,
).join(",\n");
const locales = Object.fromEntries(["zh-CN", "en"].map((locale) => [
  locale, JSON.parse(fs.readFileSync(path.join(root, `i18n/${locale}.json`), "utf8")),
]));
// Newer hosts embed the display SDK ahead of the kit runtime (see uiKitBundle.ts).
function kitRuntime() {
  const runtime = fs.readFileSync(path.join(kit, "runtime.js"), "utf8");
  const display = path.join(host, "static/plugin-ui-sdk/display.js");
  if (!fs.existsSync(display)) return runtime;
  return `const createPluginDisplayClient = (function () {
const module = { exports: {}, nekoPluginDisplayFactoryOnly: true };
${fs.readFileSync(display, "utf8")}
return module.exports;
})();
${runtime}`;
}
const safeJson = (value) => JSON.stringify(value).replaceAll("<", "\\u003c");
const html = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sticker Manager - Offline UI Preview</title>
<style>${fs.readFileSync(path.join(kit, "styles.css"), "utf8")}</style></head><body>
<main id="root"></main><script>
window.__NEKO_PAYLOAD = {locale:"zh-CN",i18n:{locale:"zh-CN",default_locale:"zh-CN",messages:${safeJson(locales)}}};
${kitRuntime()}
const fixture = ${safeJson(fixture)};
const factories = {${factories}};
const cache = {};
function load(name) {
  if (cache[name]) return cache[name].exports;
  const module = cache[name] = {exports:{}};
  factories[name](function(specifier) {
    if (specifier === "@neko/plugin-ui") return window.NekoUiKit;
    const parts = name.split("/").slice(0,-1);
    specifier.split("/").forEach(part => part === ".." ? parts.pop() : part !== "." && parts.push(part));
    const base = parts.join("/");
    return load(factories[base+".tsx"] ? base+".tsx" : base+".ts");
  },module,module.exports);
  return module.exports;
}
window.previewCalls = [];
const surface = {
  state: fixture.state, t: window.NekoUiKit.t,
  api: {
    async call(action,args) {
      window.previewCalls.push({action,args});
      if (action === "preview") return {result:{mime:"image/png",
        chunk_base64:fixture.images[args.id],done:true,next_offset:0}};
      if (action === "set_send_cooldown") surface.state.config.cooldown_sec = args.cooldown_sec;
      if (action === "set_send_probability") surface.state.config.probability = args.probability;
      if (action === "set_reminder_interval") surface.state.awareness.min_interval_sec = args.min_interval_sec;
      if (action === "switch") surface.state.enabled = args.enabled;
      if (action === "update") Object.assign(surface.state.stickers.find(row => row.id === args.id), args);
      return {result: action === "set_send_cooldown" ? {cooldown_sec:args.cooldown_sec}
        : action === "set_send_probability" ? {probability:args.probability}
        : action === "test_selection" ? {status:"selected"} : {}};
    },
    async refresh() { draw(); }
  }
};
const Panel = load("ui/panel.tsx").default;
function draw() { window.NekoUiKit.render(window.NekoUiKit.h(Panel,surface),document.getElementById("root")); }
window.previewLocale = function(locale) {
  __NEKO_PAYLOAD.locale = locale;
  __NEKO_PAYLOAD.i18n.locale = locale;
  draw();
};
window.previewAttachment = function(status) {
  surface.state.eager_attachment = {status, attempts:3, sent:1};
  draw();
};
draw();
</script></body></html>`;
fs.mkdirSync(output, { recursive: true });
const previewFile = path.join(output, "index.html");
fs.writeFileSync(previewFile, html);

async function layout(page) {
  const result = await page.evaluate(() => {
    const visible = (element) => element.getClientRects().length > 0;
    const outside = [...document.querySelectorAll("button,summary,input,select,img,h2")]
      .filter(visible).filter((element) => {
        // Horizontal scrollers (the phone-width category strip) may hold off-screen items.
        for (let node = element.parentElement; node; node = node.parentElement) {
          if (["auto", "scroll"].includes(getComputedStyle(node).overflowX) &&
              node.getBoundingClientRect().right <= innerWidth + 1) return false;
        }
        const rect = element.getBoundingClientRect();
        return rect.left < -1 || rect.right > innerWidth + 1;
      }).map((element) => element.outerHTML.slice(0, 200));
    return { width: innerWidth, scroll: document.documentElement.scrollWidth, outside };
  });
  assert.ok(result.scroll <= result.width + 1, JSON.stringify(result));
  assert.deepEqual(result.outside, []);
}

async function waitForTiles(page) {
  await page.locator(".sticker-grid img").first().waitFor();
  for (const tile of await page.locator(".sticker-tile").all()) {
    await tile.scrollIntoViewIfNeeded();
    await tile.locator("img").waitFor();
    await tile.locator("img").evaluate((image) => image.decode());
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

const calls = (page, action) => page.evaluate((name) =>
  window.previewCalls.filter((call) => call.action === name), action);

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    for (const [name, width, height, scheme] of [
      ["desktop", 1280, 900, "light"], ["tablet", 820, 900, "light"],
      ["mobile", 390, 844, "light"], ["narrow", 320, 740, "light"], ["dark", 1100, 900, "dark"],
    ]) {
      const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme });
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(pathToFileURL(previewFile).href);
      const side = width >= 1000;

      // Overview first: health, probability, the undescribed sticker, recent sends.
      await page.getByText("需要处理", { exact: true }).first().waitFor();
      await page.locator(".sticker-recent img").first().waitFor();
      await page.getByText("约每 2 轮一张", { exact: true }).waitFor();
      // The host header already names the plugin; the character rides on the switch.
      assert.equal(await page.locator(".neko-page-title").count(), 0);
      await page.getByText("YUI 发表情包", { exact: true }).waitFor();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-overview.png`), fullPage: true, animations: "disabled" });
      await page.getByRole("button", { name: "测试选图", exact: true }).count();

      // "去补描述" lands in the library with the missing filter applied.
      await page.getByRole("button", { name: "去补描述", exact: true }).click();
      await page.getByText(/1 张图没有梗义和标签/).waitFor();
      assert.equal(await page.locator(".sticker-tile").count(), 1);
      await page.getByRole("button", { name: /^全部分类/ }).click();
      await waitForTiles(page);
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-library.png`), fullPage: true, animations: "disabled" });

      // Category list filters the grid.
      await page.locator(".sticker-catnav .sticker-nav-item").nth(1).click();
      assert.equal(await page.locator(".sticker-category").count(), 1);
      await page.getByRole("button", { name: /^全部分类/ }).click();

      // Detail: beside the grid on wide panels, replacing it on narrow ones.
      await page.locator(".sticker-tile button").first().click();
      if (side) {
        await page.getByRole("button", { name: "关闭详情", exact: true }).waitFor();
        assert.ok(await page.locator(".sticker-grid").first().isVisible());
        await layout(page);
        await page.screenshot({ path: path.join(output, `${name}-detail.png`), fullPage: true, animations: "disabled" });
        await page.locator(".sticker-tile button").nth(2).click();
        await page.getByRole("button", { name: "关闭详情", exact: true }).click();
      } else {
        await page.getByRole("button", { name: "返回图库", exact: true }).waitFor();
        await layout(page);
        await page.screenshot({ path: path.join(output, `${name}-detail.png`), fullPage: true, animations: "disabled" });
        await page.getByRole("button", { name: "返回图库", exact: true }).click();
      }
      assert.equal(await page.locator(".sticker-detail").count(), 0);

      // Selection mode: checkboxes only appear after "选择"; batch bar follows the selection.
      assert.equal(await page.locator('.sticker-tile input[type="checkbox"]').count(), 0);
      await page.getByRole("button", { name: "选择", exact: true }).click();
      await page.getByText("点图片勾选，可批量移动、加删标签、停用或删除。", { exact: true }).waitFor();
      await page.locator(".sticker-tile button").first().click();
      const batch = page.locator(".sticker-batch");
      await batch.getByText("已选 1 张").waitFor();
      await batch.locator("summary").click();
      assert.equal(await batch.getByRole("button", { name: "加标签", exact: true }).isDisabled(), true);
      await batch.locator("input").fill("温柔");
      assert.equal(await batch.getByRole("button", { name: "删标签", exact: true }).isEnabled(), true);
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-select.png`), fullPage: true, animations: "disabled" });
      await page.getByRole("button", { name: "完成选择", exact: true }).click();
      assert.equal(await page.locator(".sticker-batch").count(), 0);

      // New category form from the list.
      await page.getByRole("button", { name: "＋ 新建分类", exact: true }).click();
      await page.getByRole("button", { name: "创建分类", exact: true }).waitFor();
      await layout(page);
      await page.getByRole("button", { name: "取消", exact: true }).click();

      // Import page.
      await page.getByRole("tab", { name: "导入", exact: true }).click();
      await page.getByText("把图片或 zip 套图包拖到这里", { exact: true }).waitFor();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-import.png`), fullPage: true, animations: "disabled" });

      // Settings: probability and cooldown up front, diagnostics folded.
      await page.getByRole("tab", { name: "设置", exact: true }).click();
      await layout(page);
      assert.equal(await page.locator(".sticker-advanced[open]").count(), 0);
      await page.locator('input[type="number"]').fill("5");
      await page.getByRole("button", { name: "保存发送冷却", exact: true }).click();
      await page.getByText("发送冷却已保存", { exact: true }).waitFor();
      assert.equal((await calls(page, "set_send_cooldown")).length, 1);
      await page.locator(".sticker-advanced > summary").last().click();
      await layout(page);
      await page.screenshot({ path: path.join(output, `${name}-settings.png`), fullPage: true, animations: "disabled" });

      if (name === "mobile") {
        await page.evaluate(() => window.previewLocale("en"));
        await page.getByRole("tab", { name: "Overview", exact: true }).click();
        await layout(page);
        await page.screenshot({ path: path.join(output, "mobile-overview-en.png"), fullPage: true, animations: "disabled" });
      }
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log(`Visual and browser checks passed. Preview: ${previewFile}`);
  } finally {
    await browser.close();
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
