#!/usr/bin/env python3
"""一次性接力：仅原00–08全部成功后续接用户批准的09/10，不恢复失败暂停。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.superpowers/autodev'
BASE = ['00-foundation', '01-auth', '02-conversations', '03-artifacts', '04-tasks',
        '05-cli', '06-ui', '07-e2e', '08-audit']
TARGET = 'coding:loop'


def decision(state, pane, expected_pid, *, stopped=False):
    if stopped:
        return 'stop:收到STOP，不续接'
    if state.get('controller_pid') != expected_pid or pane['pid'] != expected_pid:
        return 'stop:原控制器已被替换，不干预'
    if (state.get('worktree') != str(ROOT) or state.get('provider') != 'openai-codex'
            or state.get('model') != 'gpt-6-astra'):
        return 'stop:工作区或模型不符'
    if state.get('status') == 'paused':
        return 'stop:开发已暂停，不自动恢复'
    if state.get('status') == 'running':
        return 'stop:控制器异常退出' if pane['dead'] else 'wait'
    result = state.get('last_result') or {}
    if (state.get('status') != 'completed' or state.get('phase') != 'finished'
            or state.get('completed_stages') != BASE or state.get('child_pid') is not None
            or result.get('ok') is not True or result.get('result', {}).get('status') != 'pass'):
        return 'stop:没有完整成功证据'
    if not pane['dead']:
        return 'wait'
    return 'resume' if pane['exit'] == 0 else 'stop:控制器退出码非零'


def inspect_pane():
    text = subprocess.check_output(['tmux', '-L', 'omniflow-dev', 'display-message',
        '-p', '-t', TARGET, '#{pane_dead}|#{pane_dead_status}|#{pane_pid}'], text=True, timeout=5)
    dead, status, pid = text.strip().split('|')
    return {'dead': dead == '1', 'exit': int(status) if status else None, 'pid': int(pid)}


def record(status, reason):
    path = RUN / 'handoff.json'
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'status': status, 'reason': reason, 'time': time.time()},
                              ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    print(reason, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-controller', required=True, type=int)
    parser.add_argument('--seconds', type=int, default=21600)
    args = parser.parse_args()
    if args.expected_controller <= 0 or not 1 <= args.seconds <= 21600:
        parser.error('等待最多6小时，控制器PID必须明确')
    os.umask(0o077)
    with (RUN / 'handoff.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('已有一次性接力器，不重复启动')
        from autodev import STAGES
        if [stage[0] for stage in STAGES] != BASE + ['09-runtime', '10-release']:
            raise SystemExit('新计划与已批准的两个追加阶段不符')
        end = time.monotonic() + args.seconds
        record('waiting', '等待原00–08正常完成；失败/暂停/STOP不自动续跑；最多6小时。')
        while time.monotonic() < end:
            state = json.loads((RUN / 'status.json').read_text())
            action = decision(state, inspect_pane(), args.expected_controller,
                              stopped=(RUN / 'STOP').exists())
            if action.startswith('stop:'):
                record('stopped', action)
                return 2
            if action == 'resume':
                # 再读一次；不用-k，绝不杀活窗格。新控制器自身还会取得controller.lock。
                state = json.loads((RUN / 'status.json').read_text())
                if decision(state, inspect_pane(), args.expected_controller,
                            stopped=(RUN / 'STOP').exists()) != 'resume':
                    record('stopped', '续接前状态变化，停止，不自动重试。')
                    return 2
                subprocess.run(['tmux', '-L', 'omniflow-dev', 'respawn-pane', '-t', TARGET,
                    '-c', str(ROOT), '/usr/bin/python3 -u tools/autodev.py --resume'],
                    check=True, timeout=10)
                record('started', '已一次性续接09/10生产接线与发布工具开发；不是实际生产发布。')
                return 0
            time.sleep(5)
        record('expired', '等待6小时到期，未续接；不无限等待或恢复失败。')
        return 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record('error', '接力检查失败，未自动重试：' + type(exc).__name__)
        raise SystemExit(2)
