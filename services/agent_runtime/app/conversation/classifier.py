from __future__ import annotations

import re
import unicodedata

from services.agent_runtime.app.conversation.models import (
    ConversationIntent,
)


class DeterministicConversationIntentClassifier:
    """
    Conservative deterministic intent classifier.

    Write-capable intents are explicit and are never delegated to an LLM in v1.
    """

    _RULES = (
        (
            ConversationIntent.APPROVE,
            (
                "批准",
                "同意执行",
                "同意修复",
                "确认执行",
                "approve",
                "approved",
            ),
        ),
        (
            ConversationIntent.REJECT,
            (
                "拒绝",
                "不要执行",
                "取消修复",
                "reject",
                "rejected",
                "do not execute",
            ),
        ),
        (
            ConversationIntent.REMEDIATE,
            (
                "帮我修",
                "修一下",
                "执行修复",
                "开始修复",
                "自动修复",
                "remediate",
                "fix it",
                "heal it",
            ),
        ),
        (
            ConversationIntent.VERIFICATION,
            (
                "验证结果",
                "验证怎么样",
                "修复验证",
                "verification",
                "verify result",
                "recovery check",
            ),
        ),
        (
            ConversationIntent.EVIDENCE,
            (
                "证据",
                "依据",
                "为什么这么判断",
                "你看到了什么",
                "evidence",
                "supporting evidence",
            ),
        ),
        (
            ConversationIntent.RCA,
            (
                "根因",
                "原因是什么",
                "为什么会",
                "为什么是",
                "rca",
                "root cause",
                "why did",
            ),
        ),
        (
            ConversationIntent.NEXT_STEP,
            (
                "下一步",
                "怎么办",
                "建议怎么",
                "怎么处理",
                "what next",
                "next step",
                "recommend",
            ),
        ),
        (
            ConversationIntent.STATUS,
            (
                "状态",
                "进展",
                "现在怎么样",
                "处理到哪",
                "恢复了吗",
                "status",
                "progress",
                "current state",
            ),
        ),
        (
            ConversationIntent.HELP,
            (
                "帮助",
                "你能做什么",
                "怎么用",
                "help",
                "what can you do",
            ),
        ),
    )

    def classify(self, text: str) -> ConversationIntent:
        normalized = self._normalize(text)

        for intent, phrases in self._RULES:
            for phrase in phrases:
                if self._contains(
                    normalized,
                    self._normalize(phrase),
                ):
                    return intent

        return ConversationIntent.UNKNOWN

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize(
            "NFKC",
            value,
        ).strip().lower()

        return re.sub(
            r"\s+",
            " ",
            normalized,
        )

    @staticmethod
    def _contains(text: str, phrase: str) -> bool:
        if not phrase:
            return False

        if re.fullmatch(r"[a-z0-9 ]+", phrase):
            return bool(
                re.search(
                    r"(?<![a-z0-9])"
                    + re.escape(phrase)
                    + r"(?![a-z0-9])",
                    text,
                )
            )

        return phrase in text
