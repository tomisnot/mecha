"""操作边界回归：执行前检查、账本忠实投影、断线后不重复执行。

运行：python checks/operation_boundary_selfcheck.py。全部使用内存替身，不连接设备。
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mecha.aggregate import Aggregator
from mecha.authority import Authority, Mode
from mecha.cockpit import activity_records, history_records
from mecha.commands import CommandRegistry, CommandSpec
from mecha.gate import Channel, Gate, accept_all
from mecha.history import Event, History
from mecha.providers.mcp_client import BackendConnectError, BackendTimeoutError


class CommandBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.history = History()
        self.calls = []
        self.registry = CommandRegistry()
        self.registry.register(CommandSpec(
            name="apply_change", description="Apply a test change",
            parameters={"properties": {}}, output_schema={"required": ["done"]},
            side_effect=True, wants_channel=True,
        ), self.handle)

    def handle(self, *, context, channel):
        self.calls.append(channel)
        return {"done": True}

    def invoke(self, mode, **kwargs):
        gate = Gate(Authority(mode), self.history, validate=accept_all)
        return self.registry.invoke("apply_change", {}, context=None, gate=gate, **kwargs)

    def test_missing_gate_rejected_before_side_effect(self):
        result = self.registry.invoke("apply_change", {}, context=None)
        self.assertEqual(result["error"]["info"]["kind"], "missing_gate")
        self.assertEqual(self.calls, [])

    def test_omitted_channel_cannot_bypass_locked_or_human_mode(self):
        for mode in (Mode.LOCKED, Mode.HUMAN):
            with self.subTest(mode=mode):
                self.assertTrue(self.invoke(mode)["is_error"])
                self.assertEqual(self.calls, [])
                self.assertEqual(self.history.events(), [])

    def test_default_channel_reaches_handler_and_audit(self):
        for mode in (Mode.AI, Mode.OPEN):
            with self.subTest(mode=mode):
                self.assertFalse(self.invoke(mode)["is_error"])
                self.assertEqual(self.calls[-1].side, "ai")
                self.assertEqual(self.history.events()[-1].actor, self.calls[-1].actor)

    def test_explicit_human_channel_still_works(self):
        human = Channel("operator", "human")
        self.assertFalse(self.invoke(Mode.HUMAN, channel=human)["is_error"])
        self.assertIs(self.calls[-1], human)

    def test_readonly_does_not_need_or_touch_gate(self):
        self.registry.register(CommandSpec(
            name="read_state", description="Read test state", parameters={},
            output_schema={"required": ["done"]}, side_effect=False,
        ), lambda *, context: {"done": True})
        result = self.registry.invoke("read_state", {}, context=None, gate=object())
        self.assertFalse(result["is_error"])


class LedgerProjectionTests(unittest.TestCase):
    def setUp(self):
        # 事件集可从裁剪后的账本读入；第一条的前像不在当前窗口内。
        self.records = [
            Event(4, "state", "set", "gain", 3, 2, operation_id="op-1", role="mutation"),
            Event(5, "history", "command.start", "", {"accepted": True},
                  operation_id="op-2", role="action", undoable=False),
            Event(6, "history", "command.inspect", "", {"found": True},
                  operation_id="op-3", role="action"),
        ]

    def events(self):
        return self.records

    def test_history_preserves_facts_and_does_not_link_empty_targets(self):
        rows = history_records(self)
        self.assertEqual(rows, [e.to_record() for e in self.records])
        self.assertEqual(rows[0]["before"], 2)
        self.assertIsNone(rows[2]["before"])
        self.assertEqual(history_records(self, 6), rows[2:])

    def test_activity_preserves_kind_action_and_correlation(self):
        rows = activity_records(self, 5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "history")
        self.assertEqual(rows[0]["op"], "command.start")
        self.assertEqual(rows[0]["operation_id"], "op-2")
        self.assertEqual(rows[0]["role"], "action")
        self.assertIs(rows[0]["undoable"], False)


class FakeBackend:
    def __init__(self, effects, failure=None):
        self.effects = effects
        self.failure = failure

    async def tools(self):
        return [{"name": "apply_change", "description": "Change", "inputSchema": {}}]

    async def call(self, name, args):
        # 模拟动作已经执行，但返回回执时断线或超时。
        self.effects.append(dict(args))
        if self.failure:
            raise self.failure
        return {"text": "done"}


class DisconnectTests(unittest.TestCase):
    def make_aggregator(self, failure, *, resync_fails=False):
        effects, resyncs = [], []
        agg = Aggregator()
        agg.attach("T", FakeBackend(effects, failure))

        def resync():
            resyncs.append(True)
            if resync_fails:
                raise RuntimeError("discovery unavailable")
            agg.attach("T", FakeBackend(effects))

        agg.wire(state_fn=lambda: {}, open_fn=lambda _: None, close_fn=lambda _: None,
                 known=["T"], resync_fn=resync)
        return agg, effects, resyncs

    def test_reconnect_does_not_replay_completed_side_effect(self):
        agg, effects, resyncs = self.make_aggregator(BackendConnectError("reply lost"))
        result = agg.registry().execute("t_apply_change", {})
        self.assertEqual(result["error"]["info"]["kind"], "backend_outcome_unknown")
        self.assertEqual(len(effects), 1)
        self.assertEqual(len(resyncs), 1)
        # 新请求可以继续使用已经恢复的后端。
        self.assertFalse(agg.registry().execute("t_apply_change", {})["is_error"])
        self.assertEqual(len(effects), 2)

    def test_discovery_failure_does_not_hide_unknown_outcome(self):
        agg, effects, _ = self.make_aggregator(
            BackendConnectError("reply lost"), resync_fails=True)
        result = agg.registry().execute("t_apply_change", {})
        self.assertEqual(result["error"]["info"]["kind"], "backend_outcome_unknown")
        self.assertEqual(len(effects), 1)

    def test_timeout_does_not_replay_or_trigger_restart(self):
        agg, effects, resyncs = self.make_aggregator(BackendTimeoutError("reply late"))
        result = agg.registry().execute("t_apply_change", {})
        self.assertEqual(result["error"]["info"]["kind"], "backend_timeout")
        self.assertEqual(len(effects), 1)
        self.assertEqual(resyncs, [])


if __name__ == "__main__":
    unittest.main()
