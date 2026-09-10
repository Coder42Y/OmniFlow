"""一次性接力测试：不运行真实tmux或模型。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import autodev_handoff as h


class HandoffTests(unittest.TestCase):
    def state(self, status='completed'):
        return {'controller_pid': 123, 'worktree': str(h.ROOT), 'provider': 'openai-codex',
                'model': 'gpt-6-astra', 'status': status, 'phase': 'finished',
                'completed_stages': h.BASE.copy(), 'child_pid': None,
                'last_result': {'ok': True, 'result': {'status': 'pass'}}}

    def pane(self, dead=True, code=0):
        return {'dead': dead, 'exit': code, 'pid': 123}

    def test_only_successful_dead_controller_can_resume(self):
        self.assertEqual(h.decision(self.state(), self.pane(), 123), 'resume')
        self.assertEqual(h.decision(self.state(), self.pane(False, None), 123), 'wait')
        self.assertEqual(h.decision(self.state('running'), self.pane(False, None), 123), 'wait')
        self.assertTrue(h.decision(self.state('running'), self.pane(), 123).startswith('stop:'))

    def test_stop_pauses_failures_or_replaced_controller_never_resume(self):
        for state, pane, stopped in [
            (self.state(), self.pane(), True), (self.state('paused'), self.pane(), False),
            (self.state(), self.pane(code=2), False),
            ({**self.state(), 'controller_pid': 456}, self.pane(), False),
            ({**self.state(), 'completed_stages': h.BASE[:-1]}, self.pane(), False),
            ({**self.state(), 'child_pid': 999}, self.pane(), False),
            ({**self.state(), 'last_result': {'ok': False}}, self.pane(), False),
            ({**self.state(), 'model': 'another'}, self.pane(), False),
            ({**self.state(), 'worktree': '/another'}, self.pane(), False),
        ]:
            with self.subTest(state=state, pane=pane, stopped=stopped):
                self.assertTrue(h.decision(state, pane, 123, stopped=stopped).startswith('stop:'))

    def test_main_only_respawns_once_without_killing_live_pane(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'RUN', Path(tmp)), \
             patch('sys.argv', ['handoff', '--expected-controller', '123', '--seconds', '1']), \
             patch.object(h, 'inspect_pane', return_value=self.pane()), \
             patch.object(h.subprocess, 'run') as run:
            (h.RUN / 'status.json').write_text(json.dumps(self.state()))
            self.assertEqual(h.main(), 0)
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertNotIn('-k', argv)
            self.assertEqual(argv[-1], '/usr/bin/python3 -u tools/autodev.py --resume')
            self.assertEqual(json.loads((h.RUN / 'handoff.json').read_text())['status'], 'started')

    def test_main_does_not_resume_paused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'RUN', Path(tmp)), \
             patch('sys.argv', ['handoff', '--expected-controller', '123', '--seconds', '1']), \
             patch.object(h, 'inspect_pane', return_value=self.pane()), \
             patch.object(h.subprocess, 'run') as run:
            (h.RUN / 'status.json').write_text(json.dumps(self.state('paused')))
            self.assertEqual(h.main(), 2)
            run.assert_not_called()

    def test_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'RUN', Path(tmp)), \
             patch('sys.argv', ['handoff', '--expected-controller', '123', '--seconds', '1']), \
             patch.object(h.time, 'monotonic', side_effect=[0, 2]), \
             patch.object(h.subprocess, 'run') as run:
            self.assertEqual(h.main(), 2)
            run.assert_not_called()
            self.assertEqual(json.loads((h.RUN / 'handoff.json').read_text())['status'], 'expired')


if __name__ == '__main__':
    unittest.main()
