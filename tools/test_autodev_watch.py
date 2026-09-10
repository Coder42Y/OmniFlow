import json
import unittest
import autodev_watch as watch


class WatchTests(unittest.TestCase):
    def test_pytest_counts(self):
        self.assertEqual(watch.test_summaries('1 failed, 624 passed in 137.61s (0:02:17)'),
                         ['测试日志：通过624项，失败1项，错误0项'])
        self.assertEqual(watch.test_summaries('61 passed in 11.85s'),
                         ['测试日志：通过61项，失败0项，错误0项'])

    def test_no_raw_error_or_token(self):
        self.assertEqual(watch.test_summaries('token=fake-test-secret 61 passed in 11.85s'), [])
        self.assertEqual(watch.test_summaries('assert "61 passed in 11.85s"'), [])

    def test_only_tool_output_is_observed(self):
        payload = {'type':'message_end','message':{'content':[{'type':'text','text':'61 passed in 11.85s'}]}}
        self.assertEqual(watch.event_summaries(json.dumps(payload)), [])
        payload = {'type':'tool_execution_end','result':{'content':[{'type':'text','text':'61 passed in 11.85s'}]}}
        self.assertEqual(len(watch.event_summaries(json.dumps(payload))), 1)

    def test_build_and_broken_event(self):
        self.assertEqual(watch.test_summaries('✓ built in 2.31s'), ['构建日志：前端构建完成'])
        self.assertEqual(watch.event_summaries('{incomplete'), [])


if __name__ == '__main__':
    unittest.main()
