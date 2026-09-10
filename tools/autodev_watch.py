#!/usr/bin/env python3
"""只读监看后台状态及测试摘要；不调用模型、不控制开发、不发送外部通知。"""
import json
import os
from pathlib import Path
import re
import time
from collections import deque
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.superpowers/autodev'
STAGES = {'00-foundation':'工程基础', '01-auth':'账号', '02-conversations':'对话与事件',
          '03-artifacts':'素材与版本', '04-tasks':'持久任务', '05-cli':'CLI与媒体接入',
          '06-ui':'前端界面', '07-e2e':'浏览器联调', '08-audit':'基础阶段代码审查',
          '09-runtime':'生产接线', '10-release':'发布工具与代码验收'}
PHASES = {'coder':'编码/自测', 'reviewer':'独立审查', 'verification':'固定测试',
          'network_backoff':'等待有限重连', 'starting':'准备', 'finished':'结束'}
STATUSES = {'running':'运行中', 'paused':'已暂停', 'completed':'计划完成，未发布'}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def test_summaries(text):
    """只输出严格识别的数字计数，不复制命令、路径、token或原始错误。"""
    result = []
    for raw in text.splitlines():
        line = ANSI.sub('', raw).strip().strip('=').strip()
        if len(line) > 180:
            continue
        if re.fullmatch(r'\d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|warnings?|errors?)'
                        r'(?:, \d+ (?:passed|failed|skipped|deselected|xfailed|xpassed|warnings?|errors?))*'
                        r'(?: in [0-9.:]+s?(?: \([0-9:]+\))?| \([0-9.ms: ]+\))', line):
            values = dict((key, int(value)) for value,key in re.findall(r'(\d+) (passed|failed|errors?)\b', line))
            if values:
                result.append(f"测试日志：通过{values.get('passed', 0)}项，失败{values.get('failed', 0)}项，错误{values.get('error', 0) + values.get('errors', 0)}项")
        elif re.fullmatch(r'✓ built in [0-9.]+(?:ms|s)', line):
            result.append('构建日志：前端构建完成')
    return result


def event_summaries(line):
    try:
        e = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(e, dict) or e.get('type') != 'tool_execution_end':
        return []
    content = e.get('result', {}).get('content', [])
    return [summary for item in content if item.get('type') == 'text'
            for summary in test_summaries(item.get('text', ''))]


def main():
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True)
    history = deque(maxlen=12)
    current_log = None
    offset = 0
    signature = None
    rendered = None
    print('【OmniFlow 实时进度】\n只读监看，不产生模型调用；聊天窗口不会自动收到推送。', flush=True)
    while True:
        try:
            state = json.loads((RUN/'status.json').read_text())
            stage = STAGES.get(state.get('stage'), '待核对阶段')
            phase = PHASES.get(state.get('phase'), '待核对状态')
            status = STATUSES.get(state.get('status'), '状态未知')
            count = len(state.get('completed_stages', []))
            sig = (state.get('status'), state.get('stage'), state.get('phase'), state.get('attempt'), count)
            messages = []
            if sig != signature:
                messages.append(f'{status}｜{stage}｜{phase}｜已验收{count}/{len(STAGES)}阶段')
                signature = sig
            candidate = (ROOT/state.get('log', '')).resolve()
            if candidate.is_relative_to(RUN.resolve()) and candidate.is_file():
                if candidate != current_log or candidate.stat().st_size < offset:
                    current_log, offset = candidate, 0
                with candidate.open('rb') as stream:
                    stream.seek(offset)
                    # 每轮有界读取；未写完的一帧等下轮，原日志不修改。
                    for _ in range(300):
                        start = stream.tell()
                        line = stream.readline(16*1024*1024 + 1)
                        if not line or not line.endswith(b'\n'):
                            offset = start
                            break
                        offset = stream.tell()
                        if candidate.suffix == '.jsonl':
                            messages.extend(event_summaries(line))
                        else:
                            messages.extend(test_summaries(line.decode('utf-8', errors='replace')))
            for message in messages:
                item = f'[{datetime.now().astimezone().isoformat(timespec="seconds")}] {stage}/{phase}：{message}'
                history.append(item)
                print(item, flush=True)
            text = '\n'.join(['【后台实时进度】', f'当前：{status}；{stage}；{phase}；已验收{count}/{len(STAGES)}阶段。',
                              f'控制器最近心跳：{state.get("heartbeat_at", "暂无")}',
                              '日志中的测试通过不等于独立验收；真实供应商验证和生产发布仍未完成。',
                              '本文件不向当前聊天或手机发送通知。', '', *history, ''])
            if text != rendered:
                tmp = RUN/'live.md.tmp'
                tmp.write_text(text)
                os.replace(tmp, RUN/'live.md')
                rendered = text
        except (OSError, ValueError, TypeError):
            print('暂时无法读取完整状态，等待下轮；未认定开发成功或失败。', flush=True)
        time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('进度监看已退出；没有停止编码任务。')
