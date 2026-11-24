"""Exercise plugin selection and receipt accounting through completion callbacks."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import PNG_BYTES, FakeBus, FakeConfig, FakeHostContext, TailClient, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.services import reply_completed


def setup(tmp_path, monkeypatch, *, tier="natural"):
    host = FakeHostContext(
        data_root=tmp_path, bus=FakeBus(), config=FakeConfig({"sticker_manager": {
            "enabled": True, "send": {"eagerness": tier, "cooldown_sec": 0, "recent_dedup_count": 0},
        }}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    plugin._library.load()
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="开心赞同", tags=["开心", "赞同"])
    assert not error
    client = TailClient()
    client.subscribe = AsyncMock(return_value={"ok": True})
    client.unsubscribe = AsyncMock(return_value={"ok": True})
    host.reply_tail = client
    create = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({
            "decision": "send", "sticker_id": sticker.id, "reason": "selected",
        }))),
    ]))
    host.models = SimpleNamespace(get_client=AsyncMock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )))
    monkeypatch.setattr(reply_completed, "read_local_json", lambda *a, **k: {"instance_id": "host-one"})
    return plugin, host, client, create, sticker


async def completed(plugin, *, number=1, role="K", user="今天好开心", attached=False):
    return (await plugin.after_reply(
        "我也好开心呀！", user, attached,
        _ctx={"host_reply": {
            "version": 1, "role": role, "reply_id": f"reply-{number}", "request_id": f"request-{number}",
            "call_id": f"callback-{number}", "invocation_id": f"invocation-{number}",
            "source": "plugin:sticker_manager", "token": "test-private-token",
        }},
    )).value


@pytest.mark.parametrize("tier", ["natural", "reserved", "eager"])
def test_callback_selects_one_image_without_tool_injection_polling_or_continuation(tmp_path, monkeypatch, run_async, tier):
    plugin, host, client, model, sticker = setup(tmp_path, monkeypatch, tier=tier)

    async def scenario():
        assert (await plugin._reply_completed.maintain())["status"] == "subscribed"
        assert (await completed(plugin))["status"] == "registered"
        assert not host.push.calls and not host.bus.conversations.calls and not host.bus.memory.calls
        assert (await plugin.on_eager_attachment()).value == {"status": "host_hook"}
        assert (await plugin.on_turns()).value["awareness"]["status"] == "host_hook"
        assert (await plugin.awareness_now_entry()).value["status"] == "host_hook"
        model.assert_awaited_once()
        request = model.await_args.kwargs
        assert request["model"] == "eager_attachment" and "tools" not in request
        assert request["stream"] is False
        assert request["max_tokens"] == 4096
        assert "已" in request["messages"][0]["content"]
        assert [call[0] for call in client.calls] == ["register"]
        assert client.calls[0][1]["reply_id"] == "reply-1"
        assert not plugin._library.read_usage()
        client.state = {"accepted": True, "status": "submitted"}
        await plugin.on_sticker_tail()
        await plugin.on_sticker_tail()
        usage = plugin._library.read_usage()
        assert len(usage) == 1 and usage[0]["source"] == "eager"
        assert usage[0]["id"] == sticker.id
        assert plugin._runstats.sent == plugin._reply_completed.sent == 1

    run_async(scenario())


def test_truncated_selection_is_visible_and_never_sent(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    model.return_value.choices[0].finish_reason = "length"

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "model_truncated"
        assert plugin._reply_completed.snapshot()["model_result"]["finish_reason"] == "length"
        assert not client.calls and not host.push.calls

    run_async(scenario())


def test_reasoning_budget_diagnostics_never_expose_reasoning_or_accept_truncated_output(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    response = model.return_value
    response.choices[0].finish_reason = "length"
    response.choices[0].message.content = ""
    response.choices[0].message.reasoning_content = "private provider reasoning"
    response.usage = SimpleNamespace(
        completion_tokens=4096, completion_tokens_details=SimpleNamespace(reasoning_tokens=4096),
    )

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "model_truncated"
        diagnostics = plugin._reply_completed.snapshot()["model_result"]
        assert diagnostics["max_tokens"] == diagnostics["reasoning_tokens"] == 4096
        assert diagnostics["has_reasoning"] is True and diagnostics["output_chars"] == 0
        assert "private provider reasoning" not in json.dumps(diagnostics)
        assert not client.calls

    run_async(scenario())


def test_selection_deduplicates_category_explanations_without_losing_image_annotations(tmp_path, monkeypatch, run_async):
    plugin, _, _, model, sticker = setup(tmp_path, monkeypatch)
    rows = [
        {"id": sticker.id, "group": "happy", "group_description": "shared category", "caption": "first", "tags": []},
        {"id": "other", "group": "happy", "group_description": "shared category", "visible_text": "second"},
    ]

    async def scenario():
        await plugin._reply_completed._select("reply", [{"role": "user_message", "text": "text only"}],
                                             rows, plugin._settings)
        content = model.await_args.kwargs["messages"][1]["content"]
        data = json.loads(content)
        assert content.count("shared category") == 1
        assert data["groups"] == {"happy": "shared category"}
        assert data["candidates"][0]["caption"] == "first"
        assert data["candidates"][1]["visible_text"] == "second"
        assert data["context"][0]["text"] == "text only"

    run_async(scenario())


def test_selection_probe_uses_bound_usage_without_sending_or_changing_reply_counters(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)

    async def scenario():
        result = (await plugin.test_selection_entry()).value
        assert result["status"] == "selected"
        assert result["output_format"] == "json"
        assert result["finish_reason"] == "stop"
        assert result["duration_ms"] >= 0
        assert model.await_args.kwargs["model"] == "eager_attachment"
        assert not client.calls and not host.push.calls
        assert plugin._reply_completed.attempts == plugin._reply_completed.received == 0

    run_async(scenario())


def test_selection_timeout_is_visible_without_waiting_for_host_deadline(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(reply_completed, "SELECTION_TIMEOUT", 0.01)

    async def slow(**kwargs):
        await asyncio.Event().wait()

    model.side_effect = slow

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "model_timeout"
        assert plugin._reply_completed.last_model_result["duration_ms"] >= 0
        assert not client.calls and not host.push.calls

    run_async(scenario())


def test_every_reply_reuses_exhausted_pool_and_overrides_rhythm_without_changing_settings(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, sticker = setup(tmp_path, monkeypatch, tier="eager")
    plugin._settings = replace(plugin._settings, send=replace(
        plugin._settings.send, every_reply=True, cooldown_sec=3600, recent_dedup_count=5,
        probability=0.0, eager_attachment_turns=10,
    ))
    original = plugin._settings
    monkeypatch.setattr(plugin._sender, "recent_sent_ids", lambda role, settings: (
        {sticker.id} if settings.send.recent_dedup_count else set()
    ))
    monkeypatch.setattr(plugin._sender, "cooldown_remaining", lambda role, settings, **kwargs: settings.send.cooldown_sec)
    model.return_value.choices[0].message.content = '{"choice":1}'

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        assert len(client.calls) == 1
        client.state = {"accepted": True, "status": "submitted"}
        await plugin.on_sticker_tail()
        assert (await completed(plugin, number=2))["status"] == "registered"
        assert len(client.calls) == 3  # register, previous receipt, register
        assert plugin._settings is original
        assert "每轮配图" in model.await_args.kwargs["messages"][0]["content"]

    run_async(scenario())


def test_every_reply_never_substitutes_a_candidate_after_model_rejects_all(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, every_reply=True))
    model.return_value.choices[0].message.content = '{"choice":"no_match"}'

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "no_match"
        assert not client.calls

    run_async(scenario())


@pytest.mark.parametrize("reason", ["text_only", "serious_task"])
def test_every_reply_respects_explicit_model_restrictions(tmp_path, monkeypatch, run_async, reason):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, every_reply=True))
    model.return_value.choices[0].message.content = json.dumps({
        "decision": "ineligible", "sticker_id": "", "reason": reason,
    })

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == reason
        assert not client.calls

    run_async(scenario())


def test_every_reply_does_not_send_after_timeout_truncation_or_invalid_json(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, every_reply=True))

    async def scenario():
        await plugin._reply_completed.maintain()
        model.return_value.choices[0].finish_reason = "length"
        assert (await completed(plugin, number=1))["status"] == "model_truncated"
        model.return_value.choices[0].finish_reason = "stop"
        model.return_value.choices[0].message.content = "{"
        assert (await completed(plugin, number=2))["status"] == "invalid_result"
        model.side_effect = TimeoutError
        assert (await completed(plugin, number=3))["status"] == "model_timeout"
        assert not client.calls

    run_async(scenario())


@pytest.mark.parametrize("user", ["这轮纯文字", "不要发图", "no images please", "text-only"])
def test_text_only_skips_before_model_request(tmp_path, monkeypatch, run_async, user):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin, user=user))["status"] == "text_only"
        model.assert_not_awaited()
        assert not client.calls and not host.push.calls

    run_async(scenario())


def test_duplicate_and_already_attached_do_not_select_again(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin, attached=True))["status"] == "already_attached"
        assert (await completed(plugin))["status"] == "duplicate"
        model.assert_not_awaited()
        assert not client.calls

    run_async(scenario())


def test_managed_mode_removes_tools_and_refuses_stale_model_send(tmp_path, monkeypatch, run_async):
    plugin, host, _, model, sticker = setup(tmp_path, monkeypatch)
    tools = [{"name": "sticker_send"}, {"name": "sticker_list"}]
    monkeypatch.setattr(plugin, "list_llm_tools", lambda: list(tools), raising=False)
    monkeypatch.setattr(plugin, "unregister_llm_tool", lambda name: tools.remove(
        next(row for row in tools if row["name"] == name),
    ), raising=False)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert not tools
        assert (await plugin._sticker_send_body(sticker_id=sticker.id))["reason"] == "reply_hook_managed"
        assert not host.push.calls
        assert (await plugin.send_entry(id=sticker.id, _ctx={"lanlan_name": "K"})).is_ok()
        assert len(host.push.calls) == 1
        model.assert_not_awaited()

    run_async(scenario())


@pytest.mark.parametrize("status", [404, 503])
def test_capability_failure_selects_legacy_only_on_explicit_unsupported(tmp_path, monkeypatch, run_async, status):
    plugin, _, client, _, _ = setup(tmp_path, monkeypatch)
    error = RuntimeError("private error")
    error.response = SimpleNamespace(status_code=status)
    client.subscribe.side_effect = error
    run_async(plugin._reply_completed.maintain())
    assert plugin._reply_completed.mode == ("legacy" if status == 404 else "pending")


def test_watch_resubscribes_only_when_main_instance_changes(tmp_path, monkeypatch, run_async):
    plugin, _, client, _, _ = setup(tmp_path, monkeypatch)
    instance = ["host-one"]
    monkeypatch.setattr(reply_completed, "read_local_json", lambda *a, **k: {"instance_id": instance[0]})

    async def scenario():
        await plugin._reply_completed.maintain()
        await plugin._reply_completed.maintain()
        client.subscribe.assert_awaited_once()
        instance[0] = "host-two"
        await plugin._reply_completed.maintain()
        assert client.subscribe.await_count == 2

    run_async(scenario())


@pytest.mark.parametrize("operation", ["disable", "close"])
def test_configuration_or_shutdown_cancels_selection_without_late_send(tmp_path, monkeypatch, run_async, operation):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)

    async def scenario():
        entered = asyncio.Event()

        async def slow(**_):
            entered.set()
            await asyncio.Event().wait()

        model.side_effect = slow
        await plugin._reply_completed.maintain()
        task = asyncio.create_task(completed(plugin))
        await entered.wait()
        if operation == "close":
            await plugin._reply_completed.close()
            client.unsubscribe.assert_awaited_once()
        else:
            plugin._settings = replace(plugin._settings, enabled=False)
            plugin._reply_completed.invalidate()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.calls

    run_async(scenario())


def gateway_error(code):
    error = RuntimeError("private gateway error")
    error.body = {"error": {"code": code, "message": "private"}}
    return error


def tool_registry(plugin, monkeypatch):
    tools = [{"name": "sticker_send", "description": "", "parameters": {}}, {"name": "sticker_list"}]
    monkeypatch.setattr(plugin, "list_llm_tools", lambda: [dict(row) for row in tools], raising=False)
    monkeypatch.setattr(plugin, "unregister_llm_tool", lambda name: tools.remove(
        next(row for row in tools if row["name"] == name),
    ), raising=False)

    def register(*, name, **kwargs):
        tools.append({"name": name, **kwargs})
        return True

    monkeypatch.setattr(plugin, "register_llm_tool", register, raising=False)
    return tools


@pytest.mark.parametrize(("code", "status"), [
    ("model_usage_not_bound", "model_unbound"),
    ("host_api_not_configured", "host_model_unconfigured"),
    ("host_api_unsupported", "host_model_unsupported"),
    ("agent_quota_exceeded", "model_quota_exceeded"),
    ("model_defaults_incompatible", "model_defaults_incompatible"),
    ("something_new", "model_unavailable"),
])
def test_gateway_error_codes_map_to_actionable_statuses(tmp_path, monkeypatch, run_async, code, status):
    plugin, _, _, model, _ = setup(tmp_path, monkeypatch)
    model.side_effect = gateway_error(code)
    result = run_async(plugin._reply_completed.test_selection())
    assert result["status"] == status
    assert "private" not in json.dumps(result)


def test_persistent_model_failure_hands_sending_back_to_chat_tools(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, sticker = setup(tmp_path, monkeypatch)
    tools = tool_registry(plugin, monkeypatch)
    model.side_effect = gateway_error("model_usage_not_bound")
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        assert state.mode == "hook" and not tools
        assert (await completed(plugin))["status"] == "model_unbound"
        assert not client.calls and not host.push.calls
        # The tick after a persistent failure unsubscribes before restoring tools.
        await state.maintain()
        client.unsubscribe.assert_awaited_once()
        assert state.mode == "fallback" and not state.managed
        assert {row["name"] for row in tools} == {"sticker_send", "sticker_list"}
        assert state.snapshot()["fallback_reason"] == "model_unbound"
        # Late callbacks after unsubscribing never select or send.
        assert (await completed(plugin, number=2))["status"] == "invalid_context"
        assert (await plugin._sticker_send_body(sticker_id=sticker.id)).get("reason") != "reply_hook_managed"
        # No re-probe before the retry delay.
        calls = model.await_count
        await state.maintain()
        assert model.await_count == calls and client.subscribe.await_count == 1

    run_async(scenario())


def test_uncertain_unsubscribe_keeps_tools_withdrawn(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    tools = tool_registry(plugin, monkeypatch)
    model.side_effect = gateway_error("host_api_not_configured")
    client.unsubscribe.side_effect = RuntimeError("lost response")
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        await completed(plugin)
        await state.maintain()
        assert state.mode == "hook" and not tools
        client.unsubscribe.side_effect = None
        await state.maintain()
        assert state.mode == "fallback" and tools

    run_async(scenario())


def test_transient_failures_fall_back_only_after_consecutive_limit(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    model.side_effect = gateway_error("upstream_error")
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        for number in range(1, reply_completed.TRANSIENT_FAILURE_LIMIT):
            assert (await completed(plugin, number=number))["status"] == "model_unavailable"
            await state.maintain()
            assert state.mode == "hook"
        await completed(plugin, number=reply_completed.TRANSIENT_FAILURE_LIMIT)
        await state.maintain()
        assert state.mode == "fallback"
        client.unsubscribe.assert_awaited_once()

    run_async(scenario())


def test_model_answer_resets_transient_failure_count(tmp_path, monkeypatch, run_async):
    plugin, _, _, model, _ = setup(tmp_path, monkeypatch)
    answer = model.return_value
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        for number in range(1, 10):
            model.side_effect = gateway_error("upstream_error") if number % 2 else None
            model.return_value = answer
            await completed(plugin, number=number)
            await state.maintain()
        assert state.mode == "hook"

    run_async(scenario())


def test_fallback_recovers_after_successful_probe(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    tools = tool_registry(plugin, monkeypatch)
    model.side_effect = gateway_error("agent_quota_exceeded")
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        await completed(plugin)
        await state.maintain()
        assert state.mode == "fallback"
        # Retry delay elapsed but the model still fails: stay on chat tools.
        state._retry_at = 0.0
        await state.maintain()
        assert state.mode == "fallback" and tools and client.subscribe.await_count == 1
        assert state._retry_at > 0
        # The user fixes the host model and presses the panel test.
        model.side_effect = None
        assert (await plugin.test_selection_entry()).value["status"] == "selected"
        await state.maintain()
        assert state.mode == "hook" and state.fallback_reason == "" and not tools
        assert client.subscribe.await_count == 2

    run_async(scenario())


def test_timeout_never_triggers_fallback(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    model.side_effect = TimeoutError
    state = plugin._reply_completed

    async def scenario():
        await state.maintain()
        for number in range(1, 6):
            assert (await completed(plugin, number=number))["status"] == "model_timeout"
            await state.maintain()
        assert state.mode == "hook"
        client.unsubscribe.assert_not_awaited()

    run_async(scenario())


def test_queued_uncertain_registration_is_never_retried_with_push(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    client.receipt = {"accepted": False, "status": "uncertain"}

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registration_uncertain"
        assert (await completed(plugin))["status"] == "duplicate"
        assert not host.push.calls and plugin._reply_completed.registered == 0
        model.assert_awaited_once()

    run_async(scenario())


def test_invalid_context_never_reaches_model(tmp_path, monkeypatch, run_async):
    plugin, _, _, model, _ = setup(tmp_path, monkeypatch)
    result = run_async(plugin.after_reply("hello", "hello", False, _ctx={"host_reply": {"role": "K"}}))
    assert result.value["status"] == "invalid_context"
    model.assert_not_awaited()


def test_next_reply_reconciles_previous_receipt_without_waiting_for_timer(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        client.state = {"accepted": True, "status": "submitted"}
        # No on_sticker_tail timer runs between these completed replies.
        assert (await completed(plugin, number=2))["status"] == "registered"
        assert model.await_count == 2 and plugin._runstats.sent == 1
        await plugin.on_sticker_tail()
        assert plugin._runstats.sent == plugin._reply_completed.sent == 2
        assert len(plugin._library.read_usage()) == 2 and not host.push.calls

    run_async(scenario())


class _Roll:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


def test_probability_miss_skips_before_model_request(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, probability=0.5))
    roll = _Roll(0.9)
    monkeypatch.setattr(reply_completed, "_RNG", roll)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "probability_skip"
        assert roll.calls == 1
        model.assert_not_awaited()
        assert not client.calls and not host.push.calls

    run_async(scenario())


def test_probability_hit_selects_and_sender_does_not_roll_again(tmp_path, monkeypatch, run_async):
    from sticker_manager.services import sender as sender_module

    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, probability=0.5))
    roll = _Roll(0.1)
    sender_roll = _Roll(0.99)  # would decline if the sender rolled a second time
    monkeypatch.setattr(reply_completed, "_RNG", roll)
    monkeypatch.setattr(sender_module, "_RNG", sender_roll)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        assert roll.calls == 1 and sender_roll.calls == 0
        model.assert_awaited_once()

    run_async(scenario())


def test_every_reply_never_rolls(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(
        plugin._settings.send, probability=0.0, every_reply=True,
    ))
    roll = _Roll(0.9)
    monkeypatch.setattr(reply_completed, "_RNG", roll)

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        assert roll.calls == 0

    run_async(scenario())


def test_zero_probability_never_selects(tmp_path, monkeypatch, run_async):
    plugin, host, client, model, _ = setup(tmp_path, monkeypatch)
    plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, probability=0.0))
    monkeypatch.setattr(reply_completed, "_RNG", _Roll(0.0))

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "probability_skip"
        model.assert_not_awaited()

    run_async(scenario())


class _Rejected(Exception):
    def __init__(self, code):
        super().__init__("rejected")
        self.body = {"error": {"code": code}}


def test_selection_prefills_answer_and_parses_continuation(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    # Reasoning models continue the prefix with only the rest of the object.
    model.return_value.choices[0].message.content = "1}"

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        messages = model.await_args.kwargs["messages"]
        assert [m["role"] for m in messages] == ["system", "user", "assistant"]
        assert messages[-1]["content"] == reply_completed.ANSWER_PREFIX
        assert plugin._reply_completed.snapshot()["model_result"]["prefill"] == "used"
        assert len(client.calls) == 1

    run_async(scenario())


@pytest.mark.parametrize("content", ['{"choice":1}', '```json\n{"choice":1}\n```'])
def test_prefill_ignored_by_provider_still_parses_full_answer(tmp_path, monkeypatch, run_async, content):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    model.return_value.choices[0].message.content = content

    async def scenario():
        await plugin._reply_completed.maintain()
        status = (await completed(plugin))["status"]
        assert status == ("registered" if content.startswith("{") else "invalid_result")

    run_async(scenario())


def test_prefill_continuation_keywords(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    model.return_value.choices[0].message.content = '"no_match"}'

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "no_match"
        assert not client.calls

    run_async(scenario())


def test_rejected_prefill_retries_plainly_and_stays_off(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    answer = model.return_value
    answer.choices[0].message.content = '{"choice":1}'
    model.side_effect = [_Rejected("upstream_request_rejected"), answer, answer]

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "registered"
        assert model.await_count == 2
        assert model.await_args_list[0].kwargs["messages"][-1]["role"] == "assistant"
        assert model.await_args_list[1].kwargs["messages"][-1]["role"] == "user"
        assert plugin._reply_completed.snapshot()["model_result"]["prefill"] == "rejected"
        assert plugin._reply_completed._transient_failures == 0
        client.state = {"accepted": True, "status": "submitted"}
        await plugin.on_sticker_tail()
        assert (await completed(plugin, number=2))["status"] == "registered"
        assert model.await_count == 3
        assert model.await_args.kwargs["messages"][-1]["role"] == "user"
        assert plugin._reply_completed.snapshot()["model_result"]["prefill"] == "off"

    run_async(scenario())


def test_persistent_errors_do_not_disable_prefill(tmp_path, monkeypatch, run_async):
    plugin, _, client, model, _ = setup(tmp_path, monkeypatch)
    model.side_effect = _Rejected("agent_quota_exceeded")

    async def scenario():
        await plugin._reply_completed.maintain()
        assert (await completed(plugin))["status"] == "model_quota_exceeded"
        assert model.await_count == 1 and plugin._reply_completed._prefill is True

    run_async(scenario())
