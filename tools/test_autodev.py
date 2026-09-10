"""只使用假pi和本地Python子进程；不调用模型。"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('autodev', Path(__file__).with_name('autodev.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
PASS = {'ok': True, 'result': {'status': 'pass', 'summary': '测试通过', 'evidence': ['test']}}
RETRY = {'ok': True, 'result': {'status': 'retry', 'summary': '需要修复', 'evidence': ['test']}}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(a, 'ROOT', self.root), patch.object(a, 'RUN', self.root / 'run'),
                        patch.object(a, 'STATE', {}), patch.object(a, 'DEADLINE', 0),
                        patch.object(a, 'MIN_FREE_BYTES', 0)]
        for p in self.patches:
            p.start()
        a.RUN.mkdir()

    def tearDown(self):
        a.terminate_child()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_latest_scope_skips_visual_review_but_keeps_code_gates(self):
        for stage in [('06-ui', '前端'), ('07-e2e', '联调'), ('08-audit', '最终审查')]:
            for role in ('coder', 'reviewer'):
                with self.subTest(stage=stage[0], role=role):
                    prompt = a.make_prompt(stage, role, 1, None)
                    self.assertIn('取消后续视觉模型复审和外观审美验收', prompt)
                    self.assertIn('CSS媒体查询', prompt)
                    self.assertIn('仍需执行当前阶段固定回归与独立审查', prompt)
                    self.assertNotIn('生成新截图并由独立reviewer实际读图复审', prompt)
                    if stage[0] == '06-ui':
                        self.assertIn('docs/progress/06-ui-代码收尾.md', prompt)
                        self.assertNotIn('必须先读docs/design/前端视觉整改与验收-v1.md', prompt)

    def test_full_release_adds_bounded_offline_implementation_stages(self):
        self.assertEqual([s[0] for s in a.STAGES][-2:], ['09-runtime', '10-release'])
        for stage in a.STAGES[-2:]:
            for role in ('coder', 'reviewer'):
                prompt = a.make_prompt(stage, role, 1, None)
                self.assertIn('docs/plans/完整新版发布实施计划-v1.md', prompt)
                self.assertIn('不发真实生成、不改生产服务或域名', prompt)
                self.assertIn('取消后续视觉模型复审和外观审美验收', prompt)
            self.assertIn(['npm', '--prefix', 'frontend', 'run', 'test:e2e'],
                          a.gate_commands(a.STAGES.index(stage)))
        self.assertEqual(a.MAX_ATTEMPTS, 3)
        self.assertEqual(a.ROUND_SECONDS, 3600)

    def test_completion_parser(self):
        self.assertIsNone(a.parse_result('看起来都好了'))
        self.assertIsNone(a.parse_result('AUTODEV_RESULT:{"status":"pass"}'))
        self.assertIsNone(a.parse_result('AUTODEV_RESULT:{"status":"pass","summary":"x","evidence":"not-list"}'))
        self.assertEqual(a.parse_result('AUTODEV_RESULT:' + json.dumps(PASS['result']))['status'], 'pass')

    def test_stop_prevents_spawn(self):
        (a.RUN / 'STOP').touch()
        with patch.object(a.subprocess, 'Popen') as popen:
            result = a.run_process(['never'], a.RUN / 'x', 3)
        self.assertEqual(result['reason'], 'user_stop')
        popen.assert_not_called()

    def test_json_success_requires_agent_end(self):
        record = {'type': 'message_end', 'message': {'role': 'assistant', 'model': a.MODEL,
                  'provider': a.PROVIDER, 'stopReason': 'stop', 'content': [
                      {'type': 'text', 'text': 'AUTODEV_RESULT:' + json.dumps(PASS['result'])}]}}
        code = f'print({json.dumps(record)!r}); print(\'{json.dumps({"type":"agent_end"})}\')'
        result = a.run_process([sys.executable, '-c', code], a.RUN / 'ok.log', 5, True)
        self.assertTrue(result['ok'])
        result = a.run_process([sys.executable, '-c', f'print({json.dumps(record)!r})'], a.RUN / 'bad.log', 5, True)
        self.assertEqual(result['reason'], 'missing_completion_record')

    def test_nonzero_process_rejected(self):
        result = a.run_process([sys.executable, '-c', 'raise SystemExit(7)'], a.RUN / 'exit.log', 5)
        self.assertEqual(result['exit_code'], 7)
        self.assertFalse(result['ok'])

    def test_round_timeout(self):
        result = a.run_process([sys.executable, '-c', 'import time;time.sleep(20)'], a.RUN / 'timeout.log', .05)
        self.assertEqual(result['reason'], 'round_timeout')
        self.assertIsNone(a.CHILD)

    def test_model_mismatch_rejected(self):
        record = {'type':'message_end', 'message':{'role':'assistant','model':'unapproved-model','content':[]}}
        result = a.run_process([sys.executable,'-c',f'print({json.dumps(record)!r})'], a.RUN/'model.log',5,True)
        self.assertEqual(result['reason'], 'process_or_model_error')

    def stream(self, events, name='stream'):
        code = ';'.join('print(' + repr(json.dumps(e)) + ')' for e in events)
        return a.run_process([sys.executable, '-c', code], a.RUN / (name + '.log'), 5, True)

    def final_message(self):
        return {'type':'message_end', 'message':{'role':'assistant','model':a.MODEL,
                'provider':a.PROVIDER,'stopReason':'stop','content':[
                    {'type':'text','text':'AUTODEV_RESULT:' + json.dumps(PASS['result'])}]}}

    def test_pi_internal_retry_success_is_not_failure(self):
        error = {'type':'message_end','message':{'role':'assistant','model':a.MODEL,
                 'stopReason':'error','errorMessage':'WebSocket error','content':[]}}
        events = [error, {'type':'agent_end'}, {'type':'auto_retry_start'},
                  self.final_message(), {'type':'agent_end'}]
        self.assertTrue(self.stream(events)['ok'])

    def test_final_network_error_remains_failure(self):
        error = {'type':'message_end','message':{'role':'assistant','stopReason':'error',
                 'errorMessage':'WebSocket error','content':[]}}
        self.assertEqual(self.stream([error,{'type':'agent_end'}])['reason'], 'transient_network_error')

    def test_model_mismatch_cannot_be_hidden_by_success(self):
        changed = {'type':'message_end','message':{'role':'assistant','model':'other','content':[]}}
        self.assertFalse(self.stream([changed,self.final_message(),{'type':'agent_end'}])['ok'])

    def test_auth_and_usage_errors_are_not_network_retries(self):
        for text in ['WebSocket error: 401', 'WebSocket error: quota', 'billing error', '429 rate limit']:
            self.assertFalse(a.is_transient_network_error(text))
        self.assertTrue(a.is_transient_network_error('WebSocket error'))

    def test_network_retries_are_bounded(self):
        fail = {'ok':False,'reason':'transient_network_error'}
        with patch.object(a, 'NETWORK_RETRY_DELAYS', (0,0)), \
             patch.object(a, 'pi_round_once', return_value=fail) as call:
            self.assertFalse(a.pi_round(('01-auth','账号'),'coder',1,None)['ok'])
            self.assertEqual(call.call_count,3)

    def test_network_retry_can_recover(self):
        fail = {'ok':False,'reason':'transient_network_error'}
        with patch.object(a, 'NETWORK_RETRY_DELAYS', (0,0)), \
             patch.object(a, 'pi_round_once', side_effect=[fail,PASS]) as call:
            self.assertTrue(a.pi_round(('01-auth','账号'),'coder',1,None)['ok'])
            self.assertEqual(call.call_count,2)

    def test_stop_during_network_backoff(self):
        (a.RUN/'STOP').touch()
        with patch.object(a, 'NETWORK_RETRY_DELAYS', (1,1)), \
             patch.object(a, 'pi_round_once', return_value={'ok':False,'reason':'transient_network_error'}) as call:
            self.assertEqual(a.pi_round(('01-auth','账号'),'coder',1,None)['reason'],'user_stop')
            self.assertEqual(call.call_count,1)

    def test_fixed_gates_expand(self):
        self.assertEqual(len(a.gate_commands(0)), 3)
        self.assertEqual(len(a.gate_commands(6)), 5)
        self.assertEqual(len(a.gate_commands(7)), 6)

    def test_retry_then_review_success(self):
        with patch.object(a, 'STAGES', [('00-foundation','测试')]), \
             patch.object(a, 'pi_round', side_effect=[PASS, RETRY, PASS, PASS]) as calls, \
             patch.object(a, 'gates', return_value={'ok':True}), \
             patch.object(a.signal, 'signal'), patch.object(sys,'argv',['autodev']):
            self.assertEqual(a.main(), 0)
            self.assertEqual(calls.call_count,4)
        self.assertEqual(a.STATE['completed_stages'], ['00-foundation'])

    def test_failure_does_not_advance(self):
        with patch.object(a, 'STAGES', [('00-foundation','测试')]), \
             patch.object(a, 'pi_round', return_value=RETRY) as calls, \
             patch.object(a.signal,'signal'), patch.object(sys,'argv',['autodev']):
            self.assertEqual(a.main(), 2)
            self.assertEqual(calls.call_count,a.MAX_ATTEMPTS)
        self.assertEqual(a.STATE['reason'],'attempts_exhausted')
        self.assertEqual(a.STATE['completed_stages'],[])

    def test_pauses_on_auth_or_runtime_error(self):
        with patch.object(a, 'STAGES', [('00-foundation','测试')]), \
             patch.object(a, 'pi_round', return_value={'ok':False,'reason':'process_or_model_error'}), \
             patch.object(a.signal,'signal'), patch.object(sys,'argv',['autodev']):
            self.assertEqual(a.main(),2)
        self.assertEqual(a.STATE['status'],'paused')


if __name__=='__main__':
    unittest.main()
