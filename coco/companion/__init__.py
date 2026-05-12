"""coco.companion 子包：陪伴层（idle 情境化、proactive 等）。"""

from coco.companion.situational_idle import (
    IdleBias,
    IdleSituation,
    SituationalIdleConfig,
    SituationalIdleModulator,
    situational_idle_config_from_env,
    situational_idle_enabled_from_env,
)
from coco.companion.profile_switcher import (
    MultiProfileStore,
    MultiUserConfig,
    ProfileSwitcher,
    build_multi_profile_store,
    build_profile_switcher,
    multi_user_config_from_env,
    multi_user_enabled_from_env,
)

__all__ = [
    "IdleBias",
    "IdleSituation",
    "SituationalIdleConfig",
    "SituationalIdleModulator",
    "situational_idle_config_from_env",
    "situational_idle_enabled_from_env",
    "MultiProfileStore",
    "MultiUserConfig",
    "ProfileSwitcher",
    "build_multi_profile_store",
    "build_profile_switcher",
    "multi_user_config_from_env",
    "multi_user_enabled_from_env",
]
