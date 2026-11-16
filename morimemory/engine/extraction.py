"""Conversation memory extraction and classification.

Two extractors are available:

* A deterministic bilingual heuristic extractor. It never invents facts and
  marks every candidate with an explicit provenance class.
* An optional OpenAI-compatible chat model extractor for higher recall. Its
  output is validated and downgraded to ``ai_inference`` when it cannot point
  to a concrete user message.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from loguru import logger

from ..config import ConfigManager
from ..domain import MemoryCategory, MemoryType, SourceKind
from ..security import SecretBox
from .types import (
    ConversationMessage,
    EntityInput,
    EvidenceInput,
    MemoryCandidate,
    Principal,
)

CATEGORY_IMPORTANCE: dict[str, float] = {
    MemoryCategory.IDENTITY: 0.9,
    MemoryCategory.PREFERENCE: 0.72,
    MemoryCategory.RELATIONSHIP: 0.75,
    MemoryCategory.GOAL: 0.7,
    MemoryCategory.PLAN: 0.65,
    MemoryCategory.SKILL: 0.6,
    MemoryCategory.HABIT: 0.55,
    MemoryCategory.EVENT: 0.6,
    MemoryCategory.FACT: 0.65,
    MemoryCategory.OPINION: 0.5,
    MemoryCategory.CONTEXT: 0.35,
    MemoryCategory.SUMMARY: 0.6,
    MemoryCategory.OTHER: 0.4,
}

SOURCE_CONFIDENCE: dict[str, float] = {
    SourceKind.USER_EXPLICIT: 0.9,
    SourceKind.BEHAVIOR_OBSERVATION: 0.7,
    SourceKind.AI_INFERENCE: 0.55,
    SourceKind.MODEL_SUMMARY: 0.75,
}

SOURCE_CONFIDENCE_CAP: dict[str, float] = {
    SourceKind.USER_EXPLICIT: 0.98,
    SourceKind.BEHAVIOR_OBSERVATION: 0.85,
    SourceKind.AI_INFERENCE: 0.7,
    SourceKind.MODEL_SUMMARY: 0.8,
}

CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        MemoryCategory.IDENTITY,
        re.compile(
            r"(我叫|我的名字|我是[^，。,.!?？]|my name is|i am [a-z]|i'm [a-z])",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.PREFERENCE,
        re.compile(
            r"(我喜欢|我爱|我讨厌|我不喜欢|我更喜欢|我更爱|prefer|i like|i love|i hate|"
            r"i don'?t like|my favou?rite)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.RELATIONSHIP,
        re.compile(
            r"(我的朋友|我的同事|我的家人|我的妈妈|我的爸爸|我的老婆|我的老公|"
            r"我的女朋友|我的男朋友|my (friend|colleague|mother|father|wife|husband|"
            r"girlfriend|boyfriend))",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.GOAL,
        re.compile(
            r"(我想|我打算|我计划|我的目标|我准备|i want to|i plan to|i'?m going to|my goal)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.SKILL,
        re.compile(
            r"(我会|我擅长|我能|我可以|i can |i know how to|i'?m good at|i'?m skilled)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.HABIT,
        re.compile(
            r"(我每天|我经常|我通常|我总是|我习惯|every day|usually|often|always|i tend to)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.EVENT,
        re.compile(
            r"(今天|昨天|前天|上周|上个月|上次|刚才|最近|today|yesterday|last week|"
            r"last month|this morning|recently|i went|i visited|i met)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.OPINION,
        re.compile(
            r"(我觉得|我认为|在我看来|我相信|i think|i believe|in my opinion|it seems to me)",
            re.IGNORECASE,
        ),
    ),
    (
        MemoryCategory.FACT,
        re.compile(
            r"(我住在|我来自|我在.{1,30}工作|我的生日|我养了|我有一只|我有|"
            r"i live in|i'?m from|i am from|i work at|my birthday|i have a|i own)",
            re.IGNORECASE,
        ),
    ),
]

SLOT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "name": [
        re.compile(
            r"(?:我叫|我的名字(?:是|叫)?|my name is)\s*([A-Za-z\u4e00-\u9fff·]{1,24})", re.I
        ),
    ],
    "residence": [
        re.compile(
            r"(?:我住在|我住|我来自|i live in|i'?m from|i am from)\s*"
            r"([^，。,.!?？；;\s]{1,30})",
            re.I,
        ),
    ],
    "workplace": [
        re.compile(r"我在\s*([^，。,.!?？；;\s]{1,30}?)\s*(?:工作|上班)", re.I),
        re.compile(r"(?:i work at|i work for)\s*([^，。,.!?？；;\s]{1,30})", re.I),
    ],
    "birthday": [
        re.compile(
            r"(?:我的生日(?:是|在)?|my birthday is)\s*"
            r"(\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2}|[A-Za-z]+ \d{1,2}(?:st|nd|rd|th)?)",
            re.I,
        ),
    ],
    "pet": [
        re.compile(r"(?:我养了|我有一只|i have a|my pet is)\s*([^，。,.!?？；;\s]{1,20})", re.I),
    ],
}

SLOT_ENTITY_TYPES = {
    "name": "person",
    "residence": "place",
    "workplace": "organization",
    "birthday": "time",
    "pet": "animal",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;，,、])\s*|\n+")
_QUESTION_RE = re.compile(r"(？|\?|吗|呢|什么|怎么|为什么|谁|哪里|what|why|how|where|who)", re.I)


def detect_language(value: str) -> str:
    return "zh" if _CJK_RE.search(value) else "en"


def extract_slots(value: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    for slot, patterns in SLOT_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(value)
            if match:
                candidate = match.group(1).strip(" ，。,.!?？；;")
                if candidate:
                    slots[slot] = candidate.lower()
                break
    return slots


def _clip(value: str, limit: int) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _normalize_enum(value: str, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in allowed else fallback


class HeuristicExtractor:
    """Deterministic, local extraction that favours precision over recall."""

    def extract(
        self,
        messages: list[ConversationMessage],
        *,
        include_assistant: bool = False,
        min_characters: int = 6,
        max_candidates: int = 24,
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        seen: set[str] = set()

        for message in messages:
            role = (message.role or "user").lower()
            if role != "user" and not include_assistant:
                continue
            for sentence in _SENTENCE_SPLIT_RE.split(message.content):
                sentence = sentence.strip(" ，。！？；;,.!?、\t\r\n")
                if len(sentence) < min_characters:
                    continue
                if len(sentence) > 500:
                    sentence = sentence[:500]
                if _QUESTION_RE.search(sentence) and not re.search(
                    r"(我叫|我的名字|my name is)", sentence, re.I
                ):
                    continue

                category = self._category(sentence)
                if category is None:
                    if not self._is_first_person_statement(sentence):
                        continue
                    category = MemoryCategory.CONTEXT

                is_assistant = role in {"assistant", "character"}
                source_kind = SourceKind.AI_INFERENCE if is_assistant else SourceKind.USER_EXPLICIT
                scope = "auto"
                if is_assistant:
                    scope = "character"
                elif category == MemoryCategory.EVENT or category == MemoryCategory.CONTEXT:
                    scope = "conversation"

                memory_type = self._memory_type(category)
                slots = extract_slots(sentence)
                entities = self._entities(slots)
                evidence_source = (
                    SourceKind.AI_INFERENCE if is_assistant else SourceKind.USER_EXPLICIT
                )
                candidate = MemoryCandidate(
                    content=sentence,
                    title=_clip(sentence, 48),
                    memory_type=memory_type,
                    category=category,
                    source_kind=source_kind,
                    scope=scope,
                    importance=CATEGORY_IMPORTANCE.get(category, 0.4),
                    confidence=SOURCE_CONFIDENCE.get(evidence_source, 0.5),
                    tags=[category],
                    entities=entities,
                    attributes={"slots": slots, "extractor": "heuristic"},
                    occurred_at=message.created_at,
                    evidence=[
                        EvidenceInput(
                            source_kind=evidence_source,
                            message_id=message.id,
                            role=role,
                            quote=sentence,
                            observed_at=message.created_at,
                        )
                    ],
                    rationale="heuristic pattern match",
                )
                key = candidate.content.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
                if len(candidates) >= max_candidates:
                    return candidates
        return candidates

    @staticmethod
    def _category(sentence: str) -> str | None:
        for category, pattern in CATEGORY_PATTERNS:
            if pattern.search(sentence):
                return category
        return None

    @staticmethod
    def _is_first_person_statement(sentence: str) -> bool:
        if re.search(r"(我|我的|i |i'?m |my )", sentence, re.I) is None:
            return False
        return not _QUESTION_RE.search(sentence)

    @staticmethod
    def _memory_type(category: str) -> str:
        if category == MemoryCategory.EVENT:
            return MemoryType.EPISODIC
        if category == MemoryCategory.CONTEXT:
            return MemoryType.SHORT_TERM
        if category == MemoryCategory.SUMMARY:
            return MemoryType.SEMANTIC
        return MemoryType.LONG_TERM

    @staticmethod
    def _entities(slots: dict[str, str]) -> list[EntityInput]:
        entities: list[EntityInput] = []
        for slot, value in slots.items():
            entity_type = SLOT_ENTITY_TYPES.get(slot, "topic")
            entities.append(EntityInput(name=value, entity_type=entity_type, role=slot))
        return entities


class LLMExtractor:
    """Optional OpenAI-compatible extraction with strict provenance rules."""

    def __init__(self, config: ConfigManager, secret_box: SecretBox) -> None:
        self.config = config
        self.secret_box = secret_box

    def configured(self) -> bool:
        return bool(
            str(self.config.get("extraction.llm.base_url", "")).strip()
            and str(self.config.get("extraction.llm.model", "")).strip()
        )

    def _api_key(self) -> str:
        return self.secret_box.decrypt(str(self.config.get("extraction.llm.api_key_encrypted", "")))

    def _endpoint(self) -> str:
        base = str(self.config.get("extraction.llm.base_url", "")).rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def extract(
        self,
        messages: list[ConversationMessage],
        *,
        max_candidates: int,
    ) -> list[MemoryCandidate]:
        transcript = "\n".join(
            f"[{message.id or index}] {message.role}: {message.content}"
            for index, message in enumerate(messages)
        )
        system = (
            "You extract durable memories from an assistant conversation. "
            'Return strict JSON only: {"memories":[...]}. Every item must have '
            "content, title, category, memory_type, scope, source_kind, importance "
            "(0..1), confidence (0..1), tags (string array), evidence_message_id, "
            "and rationale. Allowed category values: identity, fact, preference, "
            "relationship, event, goal, plan, skill, opinion, habit, context, summary, other. "
            "Allowed memory_type values: working, short_term, long_term, episodic, semantic, associative. "
            "Allowed scope values: auto, user, character, conversation. "
            "Allowed source_kind values: user_explicit, ai_inference, behavior_observation, model_summary. "
            "Use user_explicit ONLY when the user directly stated the fact in the cited message. "
            "Never present an inference as a user fact. Prefer atomic, reusable memories. "
            "Ignore greetings, questions without a stated fact, and role-play narration."
        )
        payload = {
            "model": self.config.get("extraction.llm.model"),
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Extract at most {max_candidates} memories.\n\n{transcript}",
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = float(self.config.get("extraction.llm.timeout_seconds", 60.0) or 60.0)
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(self._endpoint(), json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()

        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or "{" not in content:
            raise ValueError("extraction model returned no JSON object")
        content = content[content.find("{") : content.rfind("}") + 1]
        parsed = json.loads(content)
        items = parsed.get("memories") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise ValueError("extraction model returned an invalid memories array")

        by_id = {str(message.id): message for message in messages if message.id}
        candidates: list[MemoryCandidate] = []
        for item in items[:max_candidates]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("content", "")).strip()
            if not text:
                continue
            message_id = str(item.get("evidence_message_id", "")).strip() or None
            source_message = by_id.get(message_id) if message_id else None
            source_kind = _normalize_enum(
                str(item.get("source_kind", "")),
                {value.value for value in SourceKind},
                SourceKind.AI_INFERENCE,
            )
            if source_message is None or source_message.role != "user":
                source_kind = SourceKind.AI_INFERENCE
            category = _normalize_enum(
                str(item.get("category", "")),
                {value.value for value in MemoryCategory},
                MemoryCategory.OTHER,
            )
            memory_type = _normalize_enum(
                str(item.get("memory_type", "")),
                {value.value for value in MemoryType},
                MemoryType.LONG_TERM,
            )
            scope = _normalize_enum(
                str(item.get("scope", "")), {"auto", "user", "character", "conversation"}, "auto"
            )
            try:
                importance = min(1.0, max(0.0, float(item.get("importance", 0.5))))
            except (TypeError, ValueError):
                importance = 0.5
            try:
                confidence = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            raw_tags = item.get("tags")
            tags = [str(tag)[:64] for tag in raw_tags] if isinstance(raw_tags, list) else []
            evidence: list[EvidenceInput] = []
            if source_message is not None:
                evidence.append(
                    EvidenceInput(
                        source_kind=source_kind,
                        message_id=source_message.id,
                        role=source_message.role,
                        quote=source_message.content[:2000],
                        observed_at=source_message.created_at,
                    )
                )
            candidates.append(
                MemoryCandidate(
                    content=text[:20000],
                    title=str(item.get("title", ""))[:255] or _clip(text, 48),
                    memory_type=memory_type,
                    category=category,
                    source_kind=source_kind,
                    scope=scope,
                    importance=importance,
                    confidence=confidence,
                    tags=tags,
                    attributes={
                        "extractor": "llm",
                        "slots": extract_slots(text),
                        "llm_rationale": str(item.get("rationale", ""))[:1000],
                    },
                    occurred_at=source_message.created_at if source_message else None,
                    evidence=evidence,
                    rationale=str(item.get("rationale", ""))[:1000],
                )
            )
        return candidates


class ExtractionService:
    """Select and run the configured extractor, with a safe fallback."""

    def __init__(self, config: ConfigManager, secret_box: SecretBox) -> None:
        self.config = config
        self.heuristic = HeuristicExtractor()
        self.llm = LLMExtractor(config, secret_box)

    async def extract(
        self,
        messages: list[ConversationMessage],
        principal: Principal,
    ) -> list[MemoryCandidate]:
        mode = str(self.config.get("extraction.mode", "heuristic")).lower()
        max_candidates = int(self.config.get("extraction.max_candidates", 24) or 24)
        include_assistant = bool(self.config.get("extraction.include_assistant", False))
        min_characters = int(self.config.get("extraction.min_characters", 4) or 4)

        if mode in {"llm", "hybrid"} and self.llm.configured():
            try:
                candidates = await self.llm.extract(messages, max_candidates=max_candidates)
                if candidates or mode == "llm":
                    return candidates
            except Exception as exc:
                logger.warning("LLM memory extraction failed, falling back to heuristic: {}", exc)

        candidates = self.heuristic.extract(
            messages,
            include_assistant=include_assistant,
            min_characters=min_characters,
            max_candidates=max_candidates,
        )
        for candidate in candidates:
            if candidate.scope == "auto" and principal.character_id is None:
                candidate.scope = (
                    "conversation" if candidate.memory_type == MemoryType.EPISODIC else "user"
                )
        return candidates


def normalize_candidate(candidate: MemoryCandidate, defaults: dict[str, Any]) -> MemoryCandidate:
    """Validate enum fields and apply conservative scoring defaults."""
    candidate.memory_type = _normalize_enum(
        candidate.memory_type,
        {value.value for value in MemoryType},
        MemoryType.LONG_TERM,
    )
    candidate.category = _normalize_enum(
        candidate.category,
        {value.value for value in MemoryCategory},
        MemoryCategory.OTHER,
    )
    candidate.source_kind = _normalize_enum(
        candidate.source_kind,
        {value.value for value in SourceKind},
        SourceKind.AI_INFERENCE,
    )
    candidate.scope = _normalize_enum(
        candidate.scope, {"auto", "user", "character", "conversation"}, "auto"
    )
    if candidate.importance is None:
        candidate.importance = CATEGORY_IMPORTANCE.get(
            candidate.category, float(defaults.get("default_importance", 0.5))
        )
    if candidate.confidence is None:
        candidate.confidence = SOURCE_CONFIDENCE.get(
            candidate.source_kind, float(defaults.get("default_confidence", 0.6))
        )
    cap = SOURCE_CONFIDENCE_CAP.get(candidate.source_kind, 0.9)
    candidate.confidence = min(candidate.confidence, cap)
    candidate.importance = max(0.0, min(1.0, candidate.importance))
    candidate.confidence = max(0.0, min(1.0, candidate.confidence))
    candidate.title = candidate.title.strip() or _clip(candidate.content, 48)
    candidate.tags = list(
        dict.fromkeys(tag.strip().lower()[:64] for tag in candidate.tags if tag.strip())
    )
    if "slots" not in candidate.attributes:
        candidate.attributes["slots"] = extract_slots(candidate.content)
    if candidate.source_kind in {SourceKind.AI_INFERENCE, SourceKind.MODEL_SUMMARY}:
        # Inference is useful but must remain visibly weaker than user fact.
        candidate.importance = min(candidate.importance, 0.85)
    return candidate
