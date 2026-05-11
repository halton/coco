"""coco.robot — robot 子系统聚合包。

robot-003：表情序列编排（ExpressionSequence / ExpressionPlayer）。
真正的 SDK 调用走 ``coco.actions``（goto_target / look_left / look_right）；
本包负责把多步动作组合成"剧本"，并与 IdleAnimator 协调。
"""

from coco.robot.expressions import (
    ExpressionFrame,
    ExpressionSequence,
    ExpressionPlayer,
    ExpressionsConfig,
    EXPRESSION_LIBRARY,
    expressions_config_from_env,
)

__all__ = [
    "ExpressionFrame",
    "ExpressionSequence",
    "ExpressionPlayer",
    "ExpressionsConfig",
    "EXPRESSION_LIBRARY",
    "expressions_config_from_env",
]
