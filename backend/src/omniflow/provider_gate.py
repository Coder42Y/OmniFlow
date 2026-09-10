"""可信宿主注入的短期证据，不读取凭据，也没有环境变量/HTTP 放行开关。

real 入口通过 runtime_config.SignedEvidence 接入账号核验者的签名记录；
公开价格页、模型自报及本地字段缺失都不是证据。无证据默认拒绝，测试只用合成记录。
共享许可不在此增加为测试前置。
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from .auth_security import fail

MODELS = {
    "text": "gemini-3.8-flash-low",
    "image": "agnes-image-2.5-flash",
    "ai_video": "agnes-video-2.5-flash",
    "local_motion": "local-ffmpeg",
}


@dataclass(frozen=True)
class AccessEvidence:
    model: str
    checked_at: float
    expires_at: float
    available: bool = False
    free: bool = False
    overages_disabled: bool = False
    limit_reached: bool = False
    isolation_verified: bool = False


class EvidenceGate:
    def __init__(self, evidence: Callable = lambda kind: None, clock=time.time):
        self.evidence = evidence
        self.clock = clock

    def check(self, kind):
        proof = self.evidence(kind)
        current = self.clock()
        if (
            type(proof) is not AccessEvidence
            or proof.model != MODELS.get(kind)
            or not all(math.isfinite(v) for v in (proof.checked_at, proof.expires_at))
            or not proof.checked_at <= current < proof.expires_at
            or proof.expires_at - proof.checked_at > 300
            or proof.free is not True
            or proof.overages_disabled is not True
        ):
            fail(503, "FREE_ACCESS_UNCONFIRMED", "免费及超额条件未核验或已失效")
        if proof.limit_reached is not False:
            fail(503, "PROVIDER_LIMIT_REACHED", "提供方限制阻止新增生成")
        if proof.available is not True or proof.isolation_verified is not True:
            fail(503, "PROVIDER_UNAVAILABLE", "运行权限与隔离尚未核验")
        return proof
