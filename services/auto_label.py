"""自动识图的后台任务（第 2 阶段）：插件进程里一拍一张，面板只发指令、看进度。

为什么在后台定时器里跑而不是面板里跑：一次导入上百张要跑十几分钟，主人关面板、
切标签页、去聊天都不该打断它。每张识别完立刻写回图库（`Library.apply_auto_label`），
排队状态记在图自己身上，所以任何时候中断，最多丢正在看的那一张；重启后按图库续跑。

`labeling.json` 只存少量开关与计数：暂停与原因、今天用了多少、这一轮的进度、
导入后要不要自动识图。它坏了或丢了只回到默认值，不影响图库。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

from ..core.attachment_selection import PERSISTENT_MODEL_FAILURES, model_error_status
from ..core.auto_label import build_prompt, parse_answer

MODEL_USAGE = "labeling"
STATE_FILENAME = "labeling.json"
PER_IMAGE_TIMEOUT_SEC = 40
MAX_TOKENS = 1200
MAX_EDGE = 512
GIF_FRAMES = 4
DAILY_CAP_DEFAULT = 300
DAILY_CAP_MAX = 5000
CONSECUTIVE_FAILURE_LIMIT = 5
RECENT_DURATIONS = 10
# 配置类错误每张都会一样，没必要耗着：直接暂停，等主人处理。
_PAUSE_ON = {
    "model_quota_exceeded": "quota",
}

# 只给面板看的暂停原因（稳定 ASCII，文案在 i18n）。
REASONS = frozenset({"user", "quota", "daily_cap", "model", "failures", "unsupported", "pil_unavailable"})
# 这两种第二天自动继续。
_NEXT_DAY = frozenset({"quota", "daily_cap"})


def _today() -> str:
    return date.today().isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "paused": False,
        "reason": "",
        "day": _today(),
        "day_count": 0,
        "daily_cap": DAILY_CAP_DEFAULT,
        "auto_after_import": True,
        "consecutive_failures": 0,
        "recent_ms": [],
        "run": None,
        "last_error": "",
    }


def _flatten(image: Any) -> Any:
    from PIL import Image

    if image.mode in ("RGBA", "LA", "PA", "P"):
        base = image.convert("RGBA")
        canvas = Image.new("RGB", base.size, (255, 255, 255))
        canvas.paste(base, mask=base.split()[-1])
        return canvas
    return image.convert("RGB")


def encode_image(path: Path) -> tuple[str, bool]:
    """读一张图，缩到 512px 内转 PNG data URL；动图取 4 帧拼成 2×2。回 (url, 是否动图)。"""
    from PIL import Image

    with Image.open(path) as image:
        frames = int(getattr(image, "n_frames", 1) or 1)
        if frames > 1:
            picks = sorted({round(i * (frames - 1) / (GIF_FRAMES - 1)) for i in range(GIF_FRAMES)})
            tiles = []
            for index in picks:
                image.seek(index)
                tiles.append(_flatten(image.copy()))
            cell = MAX_EDGE // 2
            for tile in tiles:
                tile.thumbnail((cell, cell))
            width = max(t.width for t in tiles)
            height = max(t.height for t in tiles)
            cols = 2 if len(tiles) > 1 else 1
            rows = (len(tiles) + cols - 1) // cols
            sheet = Image.new("RGB", (width * cols, height * rows), (255, 255, 255))
            for i, tile in enumerate(tiles):
                sheet.paste(tile, ((i % cols) * width, (i // cols) * height))
            result, animated = sheet, True
        else:
            image.load()
            result = _flatten(image.copy())
            result.thumbnail((MAX_EDGE, MAX_EDGE))
            animated = False
    out = BytesIO()
    result.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii"), animated


class AutoLabeler:
    def __init__(self, plugin, library, *, logger: Any = None, encode=encode_image, clock=time.time):
        self.plugin = plugin
        self.library = library
        self.logger = logger
        self._encode = encode
        self._clock = clock
        self._lock = threading.Lock()  # 一拍还没跑完下一拍就来了：直接跳过，不并发
        self._state_lock = threading.RLock()
        self._state = _default_state()
        self._loaded = False
        self._priority: list[str] = []  # “重新识别”的那张插队（只在内存里，重启后按普通顺序）
        self.working_id = ""

    # ------------------------------------------------------------------
    # 状态文件
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.library.root / STATE_FILENAME

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001 - 坏了就回默认值，图库里的排队状态不受影响
            self._log("labeling state unreadable; using defaults")
            return
        if not isinstance(raw, dict):
            return
        state = _default_state()
        state["paused"] = bool(raw.get("paused", False))
        reason = raw.get("reason")
        state["reason"] = reason if reason in REASONS else ""
        if isinstance(raw.get("day"), str):
            state["day"] = raw["day"]
        state["day_count"] = _int(raw.get("day_count"), 0, 10**6)
        state["daily_cap"] = _int(raw.get("daily_cap"), 1, DAILY_CAP_MAX, DAILY_CAP_DEFAULT)
        state["auto_after_import"] = bool(raw.get("auto_after_import", True))
        state["consecutive_failures"] = _int(raw.get("consecutive_failures"), 0, 1000)
        recent = raw.get("recent_ms")
        if isinstance(recent, list):
            state["recent_ms"] = [_int(v, 0, 10**6) for v in recent][-RECENT_DURATIONS:]
        run = raw.get("run")
        if isinstance(run, dict):
            state["run"] = {
                "total": _int(run.get("total"), 0, 10**6),
                "done": _int(run.get("done"), 0, 10**6),
                "failed": _int(run.get("failed"), 0, 10**6),
                "started_at": _float(run.get("started_at")),
                "finished_at": _float(run.get("finished_at")),
            }
        self._state = state

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:  # noqa: BLE001 - 开关存不下只影响重启后的暂停态，不影响图库
            self._log("labeling state write failed")

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except Exception:
                pass

    def _roll_day(self) -> None:
        today = _today()
        if self._state["day"] != today:
            self._state["day"] = today
            self._state["day_count"] = 0
            if self._state["paused"] and self._state["reason"] in _NEXT_DAY:
                self._state["paused"] = False
                self._state["reason"] = ""
                self._state["consecutive_failures"] = 0
            self._save()

    # ------------------------------------------------------------------
    # 指令（面板入口调这些）
    # ------------------------------------------------------------------

    def recover(self) -> int:
        """启动时：停在“识别中”的图退回排队（上次进程在看它的时候没了）。"""
        with self._state_lock:
            self._load()
            stuck = self.library.label_ids("working")
            if stuck:
                self.library.set_label_states(stuck, "queued")
            self._sync_run_finished()
            return len(stuck)

    def supported(self) -> bool:
        return getattr(getattr(self.plugin, "ctx", None), "models", None) is not None

    def enqueue(self, ids: list[str], *, front: bool = False) -> int:
        with self._state_lock:
            self._load()
            had_queue = bool(self.library.label_ids("queued") or self.working_id)
            queued = self.library.set_label_states(ids, "queued", only=("", "done", "failed"))
            if front:
                waiting = set(self.library.label_ids("queued"))
                self._priority = [i for i in ids if i in waiting] + [i for i in self._priority if i not in ids]
            if not queued:
                return 0
            run = self._state.get("run")
            if had_queue and run and not run.get("finished_at"):
                run["total"] += len(queued)
            else:
                self._state["run"] = {
                    "total": len(queued), "done": 0, "failed": 0,
                    "started_at": self._clock(), "finished_at": 0.0,
                }
            # 主人亲手点了“开始”：之前因为失败或模型问题停下的，给一次重新开始的机会。
            if self._state["reason"] in ("failures", "model", "pil_unavailable", "unsupported"):
                self._state["paused"] = False
                self._state["reason"] = ""
            self._state["consecutive_failures"] = 0
            self._save()
            return len(queued)

    def start_missing(self, zone_id: str = "") -> int:
        return self.enqueue(self.library.unlabeled_ids(zone_id))

    def label_one(self, sticker_id: str) -> int:
        return self.enqueue([sticker_id], front=True)

    def after_import(self, since: float) -> int:
        """导入入口收尾时调：把这次新进来、还没梗义的图排上队（开关关了就不排）。"""
        with self._state_lock:
            self._load()
            if not self._state["auto_after_import"]:
                return 0
        ids = [s.id for s in self.library.all() if s.added_at >= since and not s.caption]
        return self.enqueue(ids) if ids else 0

    def pause(self) -> None:
        with self._state_lock:
            self._load()
            self._state["paused"] = True
            self._state["reason"] = "user"
            self._save()

    def resume(self) -> None:
        with self._state_lock:
            self._load()
            self._roll_day()
            self._state["paused"] = False
            self._state["reason"] = ""
            self._state["consecutive_failures"] = 0
            self._save()

    def clear(self) -> int:
        """清空队列：没跑到的退回“没排队”，已完成的保留。"""
        with self._state_lock:
            self._load()
            cleared = self.library.set_label_states(self.library.label_ids("queued"), "", only=("queued",))
            self._priority = []
            run = self._state.get("run")
            if run and not self.working_id:
                run["total"] = run["done"] + run["failed"]
                run["finished_at"] = run["finished_at"] or self._clock()
            self._save()
            return len(cleared)

    def retry_failed(self) -> int:
        failed = self.library.label_ids("failed")
        with self._state_lock:
            self._load()
            run = self._state.get("run")
            if run and not run.get("finished_at") and failed:
                # 这一轮还没跑完：失败的重新排进本轮，不重复计数。
                run["failed"] = max(0, run["failed"] - len(failed))
                run["total"] -= len(failed)
        return self.enqueue(failed)

    def dismiss(self) -> None:
        """主人点掉“识图完成”提示。"""
        with self._state_lock:
            self._load()
            run = self._state.get("run")
            if run and run.get("finished_at"):
                self._state["run"] = None
                self._save()

    def configure(self, *, auto_after_import: Any = None, daily_cap: Any = None) -> None:
        with self._state_lock:
            self._load()
            if isinstance(auto_after_import, bool):
                self._state["auto_after_import"] = auto_after_import
            if isinstance(daily_cap, int) and not isinstance(daily_cap, bool):
                self._state["daily_cap"] = max(1, min(daily_cap, DAILY_CAP_MAX))
                if self._state["reason"] == "daily_cap" and self._state["day_count"] < self._state["daily_cap"]:
                    self._state["paused"] = False
                    self._state["reason"] = ""
            self._save()

    # ------------------------------------------------------------------
    # 面板读数
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            self._load()
            self._roll_day()
            queued = len(self.library.label_ids("queued"))
            failed_total = len(self.library.label_ids("failed"))
            state = self._state
            run = dict(state["run"]) if state.get("run") else None
            recent = state["recent_ms"]
            avg_ms = sum(recent) / len(recent) if recent else 4500
            remaining = queued + (1 if self.working_id else 0)
            if not self.supported():
                status = "unsupported"
            elif remaining and state["paused"]:
                status = "paused"
            elif remaining:
                status = "running"
            elif run and run.get("finished_at"):
                status = "finished"
            else:
                status = "idle"
            return {
                "status": status,
                "reason": state["reason"] if state["paused"] else "",
                "queued": queued,
                "working_id": self.working_id,
                "failed_total": failed_total,
                "run": run,
                "eta_sec": round(avg_ms * remaining / 1000) if remaining else 0,
                "day_count": state["day_count"],
                "daily_cap": state["daily_cap"],
                "auto_after_import": state["auto_after_import"],
                "last_error": state["last_error"],
            }

    # ------------------------------------------------------------------
    # 后台一拍
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        queued = self.library.label_ids("queued")
        if not queued:
            return ""
        allowed = set(queued)
        while self._priority:
            head = self._priority.pop(0)
            if head in allowed:
                return head
        return queued[0]

    def _pause(self, reason: str, error: str = "") -> None:
        self._state["paused"] = True
        self._state["reason"] = reason
        self._state["last_error"] = error
        self._save()
        self._log(f"auto label paused: reason={reason} error={error}")

    def _sync_run_finished(self) -> None:
        run = self._state.get("run")
        if run and not run.get("finished_at") and not self.library.label_ids("queued") and not self.working_id:
            run["finished_at"] = self._clock()
            self._save()
            self._log(f"auto label finished: done={run['done']} failed={run['failed']} total={run['total']}")

    async def tick(self) -> dict[str, Any]:
        """后台定时器每拍调一次：最多处理一张。异常一律在这里兜住（定时器没有看门狗）。"""
        if not self._lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            return await self._tick()
        except Exception:  # noqa: BLE001 - 漏出去这一拍就静默死了
            if self.logger is not None:
                self.logger.warning("auto label tick leaked", exc_info=True)
            return {"status": "leaked"}
        finally:
            if self.working_id:
                # 走到这里还挂着说明中途炸了：这张退回排队，下拍再来。
                self.library.set_label_states([self.working_id], "queued", only=("working",))
                self.working_id = ""
            self._lock.release()

    async def _tick(self) -> dict[str, Any]:
        import asyncio

        with self._state_lock:
            self._load()
            self._roll_day()
            if self._state["paused"]:
                return {"status": "paused"}
            sticker_id = self._next_id()
            if not sticker_id:
                self._sync_run_finished()
                return {"status": "idle"}
            if not self.supported():
                self._pause("unsupported")
                return {"status": "unsupported"}
            if self._state["day_count"] >= self._state["daily_cap"]:
                self._pause("daily_cap")
                return {"status": "daily_cap"}
            sticker = self.library.get(sticker_id)
            if sticker is None:
                return {"status": "skipped"}
            self.library.set_label_states([sticker_id], "working", only=("queued",))
            self.working_id = sticker_id

        try:
            data_url, animated = await asyncio.to_thread(self._encode, self.library.image_path(sticker))
        except ImportError:
            self.library.set_label_states([sticker_id], "queued", only=("working",))
            self.working_id = ""
            with self._state_lock:
                self._pause("pil_unavailable")
            return {"status": "pil_unavailable"}
        except Exception:  # noqa: BLE001 - 文件坏了或丢了：这张直接算失败，不浪费额度
            self.working_id = ""
            self.library.note_label_failure(sticker_id, final=True)
            with self._state_lock:
                self._count_result(ok=False)
            return {"status": "unreadable"}

        zone_id = self.library.zone_of_sticker(sticker)
        descs = self.library.group_descs()
        groups = {name: descs.get(name, "") for name in sorted(self.library.zone_group_names(zone_id))}
        prompt = build_prompt(groups, sticker.group, animated=animated)
        started = time.monotonic()
        status, answer = await self._ask(prompt, data_url)
        elapsed = round((time.monotonic() - started) * 1000)

        with self._state_lock:
            if status != "no_client":
                self._state["day_count"] += 1
            if status == "answered":
                self.working_id = ""
                updated, error = self.library.apply_auto_label(sticker_id, answer)
                if updated is None:
                    # 图在识别途中被删了，或写盘失败：前者直接跳过，后者回队列下次再写。
                    if error != "sticker_not_found":
                        self.library.set_label_states([sticker_id], "queued", only=("working",))
                    return {"status": "write_failed", "code": error}
                recent = (self._state["recent_ms"] + [elapsed])[-RECENT_DURATIONS:]
                self._state["recent_ms"] = recent
                self._state["consecutive_failures"] = 0
                self._state["last_error"] = ""
                self._count_result(ok=True)
                return {"status": "done", "id": sticker_id, "ms": elapsed}

            self.working_id = ""
            persistent = status in PERSISTENT_MODEL_FAILURES or status == "no_client"
            if persistent:
                # 配置或额度问题：不算这张的错，放回队列，整队暂停等主人处理（额度第二天自动续）。
                self.library.set_label_states([sticker_id], "queued", only=("working",))
                self._pause(_PAUSE_ON.get(status, "model"), status)
                return {"status": status}
            state = self.library.note_label_failure(sticker_id)
            self._state["last_error"] = status
            self._state["consecutive_failures"] += 1
            if state == "failed":
                self._count_result(ok=False)
            else:
                self._save()
            if self._state["consecutive_failures"] >= CONSECUTIVE_FAILURE_LIMIT:
                self._pause("failures", status)
            return {"status": status, "id": sticker_id}

    def _count_result(self, *, ok: bool) -> None:
        run = self._state.get("run")
        if run:
            run["done" if ok else "failed"] += 1
        self._save()
        self._sync_run_finished()

    async def _ask(self, prompt: str, data_url: str) -> tuple[str, dict[str, Any]]:
        import asyncio

        try:
            client = await self.plugin.ctx.models.get_client()
        except Exception as exc:  # noqa: BLE001 - 宿主没给客户端：按配置问题处理
            status = model_error_status(exc)
            return ("no_client" if status == "model_unavailable" else status), {}
        try:
            response = await asyncio.wait_for(client.chat.completions.create(
                model=MODEL_USAGE, stream=False, max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
            ), PER_IMAGE_TIMEOUT_SEC)
        except TimeoutError:
            return "model_timeout", {}
        except Exception as exc:  # noqa: BLE001 - 只记稳定错误码，不记供应商原文
            return model_error_status(exc), {}
        try:
            content = response.choices[0].message.content
        except Exception:  # noqa: BLE001
            return "invalid_result", {}
        answer = parse_answer(content)
        return ("answered", answer) if answer else ("invalid_result", {})


def _int(value: Any, low: int, high: int, default: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return low if default is None else default
    return max(low, min(number, high))


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if number == number and abs(number) < 1e12 else 0.0
