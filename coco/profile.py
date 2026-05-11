"""coco.profile — UserProfile 跨 session 长期记忆（companion-004）.

设计原则
========

- **本地纯 JSON**，单文件 ``~/.cache/coco/profile/profile.json``（macOS/Linux）/
  ``%LOCALAPPDATA%\\coco\\profile\\profile.json``（Windows）。非云端、无 sqlite。
- **schema_version=1** 字段为未来扩展兜底；load 时若版本不匹配 → fail-soft 返回空
  profile（不抛、不覆盖原文件，便于人工迁移）。
- **atomic write**：写 ``path.tmp`` → ``os.replace(tmp, path)``，跨平台（Python 3.3+
  os.replace 在 Windows 上原子覆盖）。
- **容量限制 + LRU 截断**：``interests ≤5``、``goals ≤3``。新值若已存在 → 移到末位
  （刷新 recency）；新值未存在且超容 → 丢弃最旧（队首）。
- **隐私**：仅存人类可读字符串（昵称/兴趣/目标）；不存 token、密码、原始音频。
- **disable kill switch**：``COCO_PROFILE_DISABLE=1`` 时 ``ProfileStore.load`` 返回
  空 profile、``save`` 直接 no-op；既不读盘也不写盘。
- **抽取**：纯 heuristic 关键词模式，无 LLM 依赖。负面前缀（我不喜欢 / 我不叫 / 我不
  想学）显式过滤。X 长度 1..15 字符；超出丢弃。

公开 API
========

- ``UserProfile``（dataclass）
- ``ProfileStore(path=None)``：load / save / update_field / add_interest / add_goal
  / set_name / reset
- ``ProfileExtractor()`` / ``extract_profile_signals(text) -> dict``
- ``build_system_prompt(profile) -> str``：把 profile 转成给 LLM 的 system prompt 前缀
- ``profile_store_disabled_from_env() -> bool``
- ``default_profile_path() -> Path``
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


log = logging.getLogger(__name__)


SCHEMA_VERSION = 1
MAX_INTERESTS = 5
MAX_GOALS = 3
MIN_X_LEN = 1
MAX_X_LEN = 15


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_profile_path() -> Path:
    """跨平台默认路径。

    - Windows: ``%LOCALAPPDATA%\\coco\\profile\\profile.json``（LOCALAPPDATA 缺省时退到
      ``~/AppData/Local``）
    - 其它（macOS/Linux）: ``~/.cache/coco/profile/profile.json``

    可被环境变量 ``COCO_PROFILE_PATH`` 完全覆盖（绝对/相对均可，相对路径相对 cwd）。
    """
    override = os.environ.get("COCO_PROFILE_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "coco" / "profile" / "profile.json"
    return Path.home() / ".cache" / "coco" / "profile" / "profile.json"


def profile_store_disabled_from_env() -> bool:
    """COCO_PROFILE_DISABLE=1 时禁用读写。"""
    return (os.environ.get("COCO_PROFILE_DISABLE") or "0").strip() in (
        "1", "true", "yes", "on"
    )


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class UserProfile:
    name: Optional[str] = None
    interests: List[str] = field(default_factory=list)  # ≤MAX_INTERESTS
    goals: List[str] = field(default_factory=list)      # ≤MAX_GOALS
    last_updated: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UserProfile":
        # 仅采纳已知字段；忽略未知字段（向前兼容）。
        return cls(
            name=d.get("name") or None,
            interests=list(d.get("interests") or []),
            goals=list(d.get("goals") or []),
            last_updated=float(d.get("last_updated") or 0.0),
            schema_version=int(d.get("schema_version") or SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ProfileStore:
    """thread-safe 单文件 JSON 持久化。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path is not None else default_profile_path()
        self._lock = threading.RLock()
        self._disabled = profile_store_disabled_from_env()

    # -------------------------------------------------- load/save
    def load(self) -> UserProfile:
        """文件不存在 / disable / 解析失败 → 返回空 profile（不抛）。"""
        if self._disabled:
            return UserProfile()
        with self._lock:
            if not self.path.exists():
                return UserProfile()
            try:
                raw = self.path.read_text(encoding="utf-8")
                obj = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                log.warning("[profile] load failed: %s: %s — 返回空 profile",
                            type(e).__name__, e)
                return UserProfile()
            if not isinstance(obj, dict):
                log.warning("[profile] root not dict (%r) — 返回空 profile", type(obj).__name__)
                return UserProfile()
            ver = int(obj.get("schema_version") or 0)
            if ver != SCHEMA_VERSION:
                log.warning(
                    "[profile] schema_version=%s 不匹配（期望 %s）— fail-soft 返回空 profile，"
                    "原文件保留待人工迁移", ver, SCHEMA_VERSION,
                )
                return UserProfile()
            return UserProfile.from_dict(obj)

    def save(self, profile: UserProfile) -> None:
        """atomic write（tmp + os.replace）。"""
        if self._disabled:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            data = json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)
            tmp.write_text(data, encoding="utf-8")
            os.replace(str(tmp), str(self.path))

    # -------------------------------------------------- mutations
    def update_field(self, **kwargs: Any) -> UserProfile:
        """部分字段更新（name / interests / goals）。立即 save 并返回新 profile。

        kwargs 支持的字段：name, interests (覆盖整个 list), goals (覆盖)。
        其它键忽略 + log warning。
        """
        with self._lock:
            p = self.load()
            for k, v in kwargs.items():
                if k == "name":
                    p.name = (v or None)
                elif k == "interests":
                    p.interests = list(v or [])[:MAX_INTERESTS]
                elif k == "goals":
                    p.goals = list(v or [])[:MAX_GOALS]
                else:
                    log.warning("[profile] update_field 忽略未知字段 %r", k)
            p.last_updated = time.time()
            self.save(p)
            return p

    def add_interest(self, item: str) -> UserProfile:
        """LRU 加入。已存在 → 移到末位；不存在且超容 → 丢最旧（队首）。"""
        return self._add_to_list("interests", item, MAX_INTERESTS)

    def add_goal(self, item: str) -> UserProfile:
        return self._add_to_list("goals", item, MAX_GOALS)

    def set_name(self, name: str) -> UserProfile:
        with self._lock:
            p = self.load()
            p.name = (name or "").strip() or None
            p.last_updated = time.time()
            self.save(p)
            return p

    def _add_to_list(self, attr: str, item: str, cap: int) -> UserProfile:
        item = (item or "").strip()
        if not item:
            return self.load()
        with self._lock:
            p = self.load()
            cur: List[str] = list(getattr(p, attr))
            # 已存在 → remove 后追加（LRU）
            if item in cur:
                cur.remove(item)
            cur.append(item)
            # 容量截断（FIFO 末位淘汰最旧 = 队首）
            while len(cur) > cap:
                cur.pop(0)
            setattr(p, attr, cur)
            p.last_updated = time.time()
            self.save(p)
            return p

    def reset(self) -> None:
        """删除 profile.json（若存在）。disable 时仍 no-op。"""
        if self._disabled:
            return
        with self._lock:
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError as e:
                    log.warning("[profile] reset unlink failed: %s", e)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


# 模式（compile 一次）
# 名字：我叫 X / 我的名字是 X / 我是 X（"我是 X" 误判风险大，仅在后跟人名感时启用 →
#       用 X 长度 1..6 限制 + 排除常见非名字词）
# 兴趣：我喜欢 X / 我对 X 感兴趣
# 目标：我想学 X / 我的目标是 X / 这周我想学 X
#
# 负面前缀过滤：句子里若先出现"不"前缀对应模式，整条丢弃（保守）。
NAME_PATTERNS = [
    re.compile(r"我叫([一-鿿 a-zA-Z0-9·]{1,15})"),
    re.compile(r"我的名字是([一-鿿 a-zA-Z0-9·]{1,15})"),
    re.compile(r"我是([一-鿿 a-zA-Z0-9·]{1,6})"),  # 短限制减少误判
]
INTEREST_PATTERNS = [
    re.compile(r"我喜欢([一-鿿 a-zA-Z0-9]{1,15})"),
    re.compile(r"我对([一-鿿 a-zA-Z0-9]{1,15})感兴趣"),
]
GOAL_PATTERNS = [
    re.compile(r"我想学([一-鿿 a-zA-Z0-9]{1,15})"),
    re.compile(r"我的目标是([一-鿿 a-zA-Z0-9]{1,15})"),
    re.compile(r"这周我想学([一-鿿 a-zA-Z0-9]{1,15})"),
]

NEGATIVE_NAME = re.compile(r"我不叫|我不是")
NEGATIVE_INTEREST = re.compile(r"我不喜欢|我不感兴趣|我对.{0,15}不感兴趣")
NEGATIVE_GOAL = re.compile(r"我不想学|我的目标不是")

# 名字常见误判黑名单（"我是学生" → 不要把"学生"当名字）
NAME_BLACKLIST = {
    "学生", "老师", "孩子", "小孩", "男孩", "女孩", "中国人", "美国人",
    "你", "他", "她", "它", "谁", "什么",
    "好人", "坏人", "好的", "对的", "对", "好",
}

# 兴趣常见误判黑名单（"我喜欢你" → 不要把"你"当兴趣）
INTEREST_BLACKLIST = {
    "你", "他", "她", "它", "我", "这个", "那个", "这", "那",
    "哥哥", "弟弟", "姐姐", "妹妹",  # 关系称谓不属"兴趣"
    "你呀", "他呀", "她呀",
}


def _trim_to_word(s: str) -> str:
    """裁掉常见结尾标点 / 助词。"""
    s = s.strip()
    # 切到第一个标点 / 空格
    for stop in "，。！？、;,. !?\n\r":
        i = s.find(stop)
        if i >= 0:
            s = s[:i]
            break
    # 去末尾常见助词
    while s and s[-1] in "的了呢啊呀吧":
        s = s[:-1]
    return s.strip()


def _valid_x(s: str) -> bool:
    return MIN_X_LEN <= len(s) <= MAX_X_LEN


def extract_profile_signals(text: str) -> Dict[str, Any]:
    """从用户输入抽 profile 信号。返回 dict（可能含 name/interests/goals 子集）。

    interests / goals 永远是 list（单 hit 也包成 list，方便后续合并）。
    名字仅取第一条 hit。
    """
    out: Dict[str, Any] = {}
    text = (text or "").strip()
    if not text:
        return out

    # name
    if not NEGATIVE_NAME.search(text):
        for pat in NAME_PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            x = _trim_to_word(m.group(1))
            if not _valid_x(x):
                continue
            if x in NAME_BLACKLIST:
                continue
            out["name"] = x
            break

    # interests
    if not NEGATIVE_INTEREST.search(text):
        interests: List[str] = []
        for pat in INTEREST_PATTERNS:
            for m in pat.finditer(text):
                x = _trim_to_word(m.group(1))
                if not _valid_x(x):
                    continue
                if x in INTEREST_BLACKLIST:
                    continue
                if x not in interests:
                    interests.append(x)
        if interests:
            out["interests"] = interests

    # goals
    if not NEGATIVE_GOAL.search(text):
        goals: List[str] = []
        for pat in GOAL_PATTERNS:
            for m in pat.finditer(text):
                x = _trim_to_word(m.group(1))
                if not _valid_x(x):
                    continue
                if x not in goals:
                    goals.append(x)
        if goals:
            out["goals"] = goals

    return out


class ProfileExtractor:
    """ProfileExtractor — 包一层提供 OO 风格的 ``extract(text)``，方便注入。"""

    def extract(self, text: str) -> Dict[str, Any]:
        return extract_profile_signals(text)


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def build_system_prompt(profile: Optional[UserProfile], base: Optional[str] = None) -> Optional[str]:
    """把 profile 转成 system prompt 前缀。

    - profile=None / 全空 → 返回 base（可为 None）
    - 否则在 base 后追加 "[用户档案]\\n名字：…\\n兴趣：…\\n学习目标：…\\n"

    为了不泄漏字段名给 LLM，使用人类可读的 zh-CN 标签。base 来自 ``coco.llm.SYSTEM_PROMPT``。
    """
    parts: List[str] = []
    if profile is not None:
        if profile.name:
            parts.append(f"用户昵称：{profile.name}")
        if profile.interests:
            parts.append("兴趣：" + "、".join(profile.interests))
        if profile.goals:
            parts.append("学习目标：" + "、".join(profile.goals))
    if not parts:
        return base
    profile_block = "[用户档案]\n" + "\n".join(parts)
    if base:
        return f"{base}\n\n{profile_block}"
    return profile_block


__all__ = [
    "UserProfile",
    "ProfileStore",
    "ProfileExtractor",
    "extract_profile_signals",
    "build_system_prompt",
    "default_profile_path",
    "profile_store_disabled_from_env",
    "SCHEMA_VERSION",
    "MAX_INTERESTS",
    "MAX_GOALS",
]
