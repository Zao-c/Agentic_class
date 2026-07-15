from dataclasses import dataclass
from typing import List

from app.schemas import RiskLevel


@dataclass(frozen=True)
class SafetyDecision:
    risk_level: RiskLevel
    must_escalate: bool
    reasons: List[str]
    restrictions: List[str]


CRITICAL_TERMS = {
    "旁路安全装置": "请求涉及绕过安全装置",
    "屏蔽安全": "请求涉及屏蔽安全功能",
    "解除联锁": "请求涉及解除安全联锁",
    "强制运动": "请求涉及强制机器人运动",
    "带电拆": "请求涉及带电拆装",
}

HIGH_RISK_TERMS = {
    "拆机": "请求涉及拆机",
    "拆控制柜": "请求涉及控制柜拆装",
    "高压": "请求涉及高压风险",
    "急停失效": "急停功能可能失效",
    "断电": "请求涉及能源隔离，需由授权人员确认",
    "制动器": "请求涉及制动系统",
    "强制输出": "请求涉及强制 I/O 输出",
}

MEDIUM_RISK_TERMS = {
    "自动运行": "请求涉及自动运行",
    "恢复系统": "系统恢复可能覆盖当前配置",
    "系统恢复": "系统恢复可能覆盖当前配置",
    "修改参数": "参数变更可能影响设备行为",
}


def check_safety(text: str, evidence_sufficient: bool = False) -> SafetyDecision:
    compact = text.replace(" ", "").lower()
    reasons: List[str] = []
    for term, reason in CRITICAL_TERMS.items():
        if term in compact:
            reasons.append(reason)
    if reasons:
        return SafetyDecision(
            RiskLevel.critical,
            True,
            reasons,
            ["不要执行所述操作", "保持设备处于安全状态", "联系教师或具备资质的专业人员"],
        )
    for term, reason in HIGH_RISK_TERMS.items():
        if term in compact:
            reasons.append(reason)
    if reasons:
        return SafetyDecision(
            RiskLevel.high,
            True,
            reasons,
            ["不要在无人监护下操作", "由授权人员执行上锁挂牌和能源确认", "转交教师或专业人员"],
        )
    for term, reason in MEDIUM_RISK_TERMS.items():
        if term in compact:
            reasons.append(reason)
    if reasons:
        return SafetyDecision(
            RiskLevel.medium,
            False,
            reasons,
            ["使用低速或单步模式验证", "确认人员与障碍物均已离开危险区域"],
        )
    if not evidence_sufficient and any(term in compact for term in ("故障", "报警", "异常")):
        return SafetyDecision(
            RiskLevel.medium,
            False,
            ["故障问题缺少充分资料依据"],
            ["不要根据猜测进行设备操作", "补充信息或转交教师"],
        )
    return SafetyDecision(RiskLevel.low, False, [], [])
