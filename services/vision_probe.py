"""识图实测（第 2 阶段步骤 0）：看绑定给“自动标注”的模型到底能不能看图。

只读：不改图库、不发聊天、不写标签。送三张图：
1. 当场生成的对照图，上面印一串随机字符——模型读对了才算真的看见了图，
   读不出来或编了别的字，说明它只是在凭空描述；
2. 图库里一张带“图中文字”的图（官方包大多有），对比已有标注；
3. 图库里一张 GIF 的首帧。

每张图单独计时；模型网关的错误只记稳定错误码，不记供应商原文。
"""

from __future__ import annotations

import base64
import json
import random
import string
import time
from io import BytesIO
from typing import Any

from ..core.attachment_selection import model_error_status

MODEL_USAGE = "labeling"
PER_IMAGE_TIMEOUT_SEC = 25
MAX_EDGE = 512

PROMPT = (
    "你在看一张聊天表情包图片。只根据图片本身回答，不要猜文件名或上下文。"
    "只返回一个 JSON 对象，不要 Markdown：\n"
    '{"visible_text":"图里清晰可见的原文，没有就空字符串",'
    '"caption":"一句中文：这张图在聊天里表达什么",'
    '"tags":["3到6个中文标签"]}'
)


def _encode(image: Any) -> str:
    """缩到 512px 以内，摊白底转 PNG，返回 data URL。"""
    from PIL import Image

    image.thumbnail((MAX_EDGE, MAX_EDGE))
    if image.mode in ("RGBA", "LA", "PA", "P"):
        base = image.convert("RGBA")
        canvas = Image.new("RGB", base.size, (255, 255, 255))
        canvas.paste(base, mask=base.split()[-1])
        image = canvas
    else:
        image = image.convert("RGB")
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _control_image(code: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=72)
    except TypeError:
        # Pillow < 10.1 只有小号位图字体：画在小图上再整体放大，字依然清楚。
        small = Image.new("RGB", (120, 50), (255, 255, 255))
        ImageDraw.Draw(small).text((10, 18), code, fill=(0, 0, 0), font=ImageFont.load_default())
        return _encode(small.resize((480, 200), Image.NEAREST))
    image = Image.new("RGB", (480, 200), (255, 255, 255))
    ImageDraw.Draw(image).text((30, 60), code, fill=(0, 0, 0), font=font)
    return _encode(image)


def _file_frame(path) -> str:
    from PIL import Image

    with Image.open(path) as image:
        image.seek(0)
        image.load()
        return _encode(image.copy())


def _parse(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    tags = data.get("tags")
    return {
        "visible_text": str(data.get("visible_text") or "")[:120],
        "caption": str(data.get("caption") or "")[:200],
        "tags": [str(tag)[:20] for tag in tags][:8] if isinstance(tags, list) else [],
    }


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


class VisionProbe:
    def __init__(self, plugin):
        self.plugin = plugin
        self.running = False

    def _samples(self) -> list[dict[str, Any]]:
        code = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        samples: list[dict[str, Any]] = [{
            "kind": "control", "name": f"对照图 {code}", "expected_text": code,
            "load": lambda: _control_image(code),
        }]
        library = self.plugin._library
        pool = [s for s in library.active_pool() if not s.disabled]
        # 官方包全是 GIF：带字那张也可以是动图（送首帧），只要和“动图”那张不是同一张。
        texted = next((s for s in pool if s.visible_text), None)
        gif = next((s for s in pool if s.file.lower().endswith(".gif") and s is not texted), None)
        for kind, sticker in (("texted", texted), ("gif", gif)):
            if sticker is None:
                continue
            path = library.image_path(sticker)
            samples.append({
                "kind": kind, "name": sticker.desc or sticker.id, "id": sticker.id,
                "expected_text": sticker.visible_text,
                "known": {"caption": sticker.caption, "tags": list(sticker.tags)[:6]},
                "load": lambda path=path: _file_frame(path),
            })
        return samples

    async def _ask(self, client, data_url: str):
        import asyncio

        return await asyncio.wait_for(client.chat.completions.create(
            model=MODEL_USAGE, stream=False, max_tokens=1200,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
        ), PER_IMAGE_TIMEOUT_SEC)

    async def run(self) -> dict[str, Any]:
        import asyncio

        if self.running:
            return {"status": "busy"}
        if not self.plugin._library.load().ok:
            return {"status": "library_unavailable"}
        self.running = True
        try:
            try:
                samples = await asyncio.to_thread(self._samples)
                for sample in samples:
                    sample["data_url"] = await asyncio.to_thread(sample.pop("load"))
            except ImportError:
                return {"status": "pil_unavailable"}
            try:
                client = await self.plugin.ctx.models.get_client()
            except Exception as exc:
                return {"status": model_error_status(exc), "code": _error_code(exc)}
            results = []
            for sample in samples:
                data_url = sample.pop("data_url")
                started = time.monotonic()
                item = {k: v for k, v in sample.items()}
                try:
                    response = await self._ask(client, data_url)
                    choice = response.choices[0]
                    content = choice.message.content
                    answer = _parse(content)
                    item["status"] = "answered" if answer else "invalid_result"
                    item["answer"] = answer
                    item["finish"] = str(getattr(choice, "finish_reason", "") or "")
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        item["tokens"] = getattr(usage, "total_tokens", None)
                        details = getattr(usage, "completion_tokens_details", None)
                        item["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
                    if not answer:
                        # 模型输出不是聊天内容，只留开头一小段用于排查格式问题。
                        item["raw_head"] = str(content or "")[:160]
                except TimeoutError:
                    item["status"] = "model_timeout"
                except Exception as exc:
                    item["status"] = model_error_status(exc)
                    item["code"] = _error_code(exc)
                item["duration_ms"] = round((time.monotonic() - started) * 1000)
                expected = sample.get("expected_text") or ""
                if expected and item.get("answer"):
                    item["text_match"] = _normalize(expected) in _normalize(item["answer"]["visible_text"])
                results.append(item)
                if item["status"] not in ("answered", "invalid_result", "model_timeout"):
                    break  # 配置类错误每张都会一样，不必再试
            control = results[0] if results else {}
            verdict = (
                "sees_images" if control.get("text_match")
                else "no_answer" if control.get("status") != "answered"
                else "blind"
            )
            self.plugin.logger.info(
                f"vision probe: verdict={verdict} statuses={[r['status'] for r in results]} "
                f"ms={[r['duration_ms'] for r in results]} codes={[r.get('code', '') for r in results]} "
                f"finish={[r.get('finish', '') for r in results]} tokens={[r.get('tokens') for r in results]} "
                f"reasoning={[r.get('reasoning_tokens') for r in results]} "
                f"text_match={[r.get('text_match') for r in results]} "
                f"raw={[r.get('raw_head', '') for r in results if r.get('raw_head')]}",
            )
            return {"status": "done", "verdict": verdict, "results": results}
        finally:
            self.running = False


def _error_code(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    code = error.get("code") if isinstance(error, dict) else ""
    return code if isinstance(code, str) else ""
