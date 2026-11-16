"""Seed a local MoriMemory instance with a small, realistic demo dataset.

Usage:
    uv run python scripts/seed_demo.py --token <service-token>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:6190/api/v1"
USER_ID = "local"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SeedError(RuntimeError):
    pass


def read_config_token() -> str:
    data_dir = Path(os.environ.get("MORIMEMORY_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    config_path = data_dir / "config.json"
    if not config_path.is_file():
        return ""
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("security", {}).get("token", "") or "")


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict | None = None,
    **kwargs,
) -> dict:
    try:
        response = client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise SeedError(f"无法连接 MoriMemory 服务 {client.base_url}: {exc}") from exc
    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")[:300]
        hint = (
            "（请检查服务令牌和 data/config.json 是否属于当前服务）"
            if response.status_code == 401
            else ""
        )
        raise SeedError(
            f"{method} {path} 返回 HTTP {response.status_code}{hint}: {body or response.reason_phrase}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        content_type = response.headers.get("content-type", "unknown")
        body = response.text.strip().replace("\n", " ")[:200]
        raise SeedError(
            f"{method} {path} 返回了非 JSON 响应（content-type={content_type}）: {body}"
        ) from exc
    if not isinstance(payload, dict):
        raise SeedError(f"{method} {path} 返回了意外的 JSON 类型")
    return payload


def wait_for_health(
    client: httpx.Client,
    wait_seconds: float,
    *,
    quiet: bool = False,
) -> dict | None:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_error = ""
    while True:
        try:
            response = client.get("/health", timeout=3.0)
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    last_error = "health 返回非 JSON 响应"
                else:
                    if payload.get("service") == "morimemory":
                        return payload
                    last_error = "端口被其他服务占用"
            else:
                last_error = f"health 返回 HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            if last_error and not quiet:
                print(f"等待服务失败：{last_error}", file=sys.stderr)
            return None
        time.sleep(0.4)


def start_local_service() -> subprocess.Popen:
    data_dir = Path(os.environ.get("MORIMEMORY_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "seed-service.log"
    log_handle = log_path.open("ab")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    print(f"服务未运行，正在启动：python -m morimemory serve（日志：{log_path}）")
    try:
        return subprocess.Popen(  # noqa: S603 - local developer helper
            [sys.executable, "-m", "morimemory", "serve"],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    finally:
        log_handle.close()


def headers(
    role: str = "admin", character: str | None = None, conversation: str | None = None
) -> dict:
    result = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Mori-User-Id": USER_ID,
        "X-Mori-Role": role,
    }
    if character:
        result["X-Mori-Character-Id"] = character
    if conversation:
        result["X-Mori-Conversation-Id"] = conversation
    return result


def post(client: httpx.Client, path: str, payload: dict, **header_kwargs) -> dict:
    response = request_json(
        client,
        "POST",
        path,
        headers=headers(**header_kwargs),
        json=payload,
    )
    return response["data"]


def create(
    client: httpx.Client,
    content: str,
    *,
    category: str = "fact",
    memory_type: str = "long_term",
    source_kind: str = "user_explicit",
    importance: float = 0.6,
    confidence: float = 0.85,
    tags: list[str] | None = None,
    scope: str = "auto",
    character: str | None = None,
    conversation: str | None = None,
    deduplicate: bool = False,
) -> str:
    data = post(
        client,
        "/memories",
        {
            "content": content,
            "category": category,
            "memory_type": memory_type,
            "source_kind": source_kind,
            "importance": importance,
            "confidence": confidence,
            "tags": tags or [],
            "scope": scope,
            "deduplicate": deduplicate,
        },
        character=character,
        conversation=conversation,
    )
    return data["memory"]["id"]


def main() -> int:
    global TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("MORIMEMORY_URL", DEFAULT_BASE))
    parser.add_argument("--token", default=os.environ.get("MORIMEMORY_TOKEN", ""))
    parser.add_argument("--reset", action="store_true", help="Delete existing spaces (demo only)")
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start `python -m morimemory serve` automatically if the service is not running",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep an auto-started service running after seeding",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=12.0,
        help="Seconds to wait for the service to become healthy (default: 12)",
    )
    args = parser.parse_args()
    TOKEN = args.token.strip() or read_config_token()
    client = httpx.Client(base_url=args.base_url, timeout=30, trust_env=False)
    service_process: subprocess.Popen | None = None
    try:
        # Only start a second process when the port is definitively not serving
        # a healthy MoriMemory instance.
        if args.start and wait_for_health(client, 0.6, quiet=True) is None:
            service_process = start_local_service()
        health = wait_for_health(client, args.wait)
        if health is None:
            print(
                f"\n无法连接 MoriMemory 服务（{args.base_url}）。\n"
                "请先在另一个终端启动：uv run morimemory serve\n"
                "或使用：uv run python scripts/seed_demo.py --reset --start\n"
                "如果刚启动，请稍后重试。\n"
                "如果终端配置了 HTTP_PROXY / ALL_PROXY，请把 127.0.0.1 加入 NO_PROXY；"
                "脚本已对本地服务禁用代理。",
                file=sys.stderr,
            )
            return 1
        if not TOKEN:
            TOKEN = read_config_token()
        if not TOKEN:
            print(
                "未找到服务令牌。请运行 `uv run morimemory token`，然后通过 --token 传入，"
                "或确认 data/config.json 属于当前运行的服务。",
                file=sys.stderr,
            )
            return 2
        print(f"service {health['status']} | fts5={health['database']['fts5']}")
        if args.reset:
            spaces = request_json(client, "GET", "/spaces", headers=headers())["data"]["items"]
            for space in spaces:
                request_json(client, "DELETE", f"/spaces/{space['id']}", headers=headers())

        post(
            client,
            "/conversations/ingest",
            {
                "messages": [
                    {
                        "id": "seed-m1",
                        "role": "user",
                        "content": (
                            "我叫林澈，是一名独立开发者。"
                            "我喜欢喝手冲咖啡，不加糖。"
                            "我住在杭州，平时喜欢在西湖边散步。"
                        ),
                    },
                    {
                        "id": "seed-m2",
                        "role": "user",
                        "content": (
                            "我正在开发 MoriBot，希望它成为一个有记忆的 AI 助手。"
                            "我养了一只叫团子的橘猫。"
                        ),
                    },
                    {
                        "id": "seed-m3",
                        "role": "user",
                        "content": ("我不喜欢太吵的环境。我每天工作到很晚，经常忘记吃饭。"),
                    },
                ],
                "async_mode": False,
            },
            character="mori",
            conversation="conv-demo",
        )

        character_ids = []
        for content, category, importance, tags in [
            (
                "森（Mori）是用户的 AI 助手，说话温和，善于观察细节。",
                "identity",
                0.95,
                ["角色", "设定"],
            ),
            (
                "森喜欢在雨天读书，也喜欢安静地听用户讲一天的见闻。",
                "preference",
                0.7,
                ["角色", "偏好"],
            ),
            ("森记得用户喝手冲咖啡时不加糖，会主动提醒。", "fact", 0.82, ["角色", "咖啡"]),
            (
                "森认为用户是一个对细节敏感、容易过度投入工作的人。",
                "opinion",
                0.55,
                ["角色", "观察"],
            ),
            (
                "森不擅长处理嘈杂的多人对话，会优先回应安静的一对一交流。",
                "habit",
                0.5,
                ["角色", "习惯"],
            ),
        ]:
            character_ids.append(
                create(
                    client,
                    content,
                    category=category,
                    source_kind="ai_inference",
                    importance=importance,
                    confidence=0.66,
                    tags=tags,
                    scope="character",
                    character="mori",
                    conversation="conv-demo",
                )
            )

        # A second character keeps its own private settings.
        yuki_id = create(
            client,
            "雪（Yuki）是另一个角色的私密设定：偏爱冷色调和冬夜。",
            category="identity",
            source_kind="ai_inference",
            importance=0.7,
            confidence=0.6,
            tags=["角色", "私密"],
            scope="character",
            character="yuki",
            conversation="conv-yuki",
        )

        memories = [
            (
                "用户最喜欢的手冲咖啡是埃塞俄比亚日晒豆，风味偏花香。",
                "preference",
                0.78,
                "user_explicit",
                ["咖啡", "偏好"],
            ),
            (
                "用户习惯在上午十点后开始写代码，凌晨才休息。",
                "habit",
                0.62,
                "behavior_observation",
                ["作息", "工作"],
            ),
            (
                "用户对玫瑰香型的香水有轻微过敏反应。",
                "fact",
                0.7,
                "user_explicit",
                ["健康", "过敏"],
            ),
            (
                "用户计划在今年冬天完成 MoriBot 的移动端内测。",
                "goal",
                0.84,
                "user_explicit",
                ["项目", "计划"],
            ),
            (
                "用户认为好的 AI 记忆系统应该允许用户检查和删除每一条记忆。",
                "opinion",
                0.76,
                "user_explicit",
                ["产品", "隐私"],
            ),
            (
                "用户擅长 Python 和 TypeScript，对 Rust 只停留在入门阶段。",
                "skill",
                0.68,
                "user_explicit",
                ["技能", "开发"],
            ),
            (
                "团子是用户养了三年的橘猫，怕生但很黏用户。",
                "fact",
                0.8,
                "user_explicit",
                ["猫", "团子"],
            ),
            (
                "用户不喜欢在周末安排工作会议。",
                "preference",
                0.6,
                "user_explicit",
                ["周末", "边界"],
            ),
            (
                "用户最近在读《设计数据密集型应用》，对分布式一致性很感兴趣。",
                "fact",
                0.66,
                "user_explicit",
                ["阅读", "技术"],
            ),
            (
                "用户希望 MoriBot 能记住角色之间的关系，而不只是单条事实。",
                "goal",
                0.8,
                "user_explicit",
                ["MoriBot", "图谱"],
            ),
            (
                "用户在手冲咖啡时习惯水温 92 度，粉水比 1:15。",
                "preference",
                0.64,
                "user_explicit",
                ["咖啡", "参数"],
            ),
            (
                "用户提到最近睡眠质量不好，通常凌晨两点后才入睡。",
                "fact",
                0.7,
                "user_explicit",
                ["睡眠", "健康"],
            ),
            (
                "用户喜欢在西湖边散步时思考产品设计。",
                "habit",
                0.58,
                "behavior_observation",
                ["杭州", "散步"],
            ),
            (
                "用户曾经在杭州的一家创业公司做过两年后端开发。",
                "fact",
                0.6,
                "user_explicit",
                ["经历", "杭州"],
            ),
            (
                "用户不太喜欢社交平台上的公开分享，更偏好小范围交流。",
                "preference",
                0.55,
                "user_explicit",
                ["社交", "隐私"],
            ),
            (
                "用户认为角色扮演中的角色应该拥有自己的记忆边界。",
                "opinion",
                0.72,
                "user_explicit",
                ["角色", "边界"],
            ),
            (
                "用户希望记忆检索默认不把 AI 推断当成用户事实。",
                "goal",
                0.8,
                "user_explicit",
                ["记忆", "可信度"],
            ),
        ]
        memory_ids = []
        for content, category, importance, source, tags in memories:
            memory_ids.append(
                create(
                    client,
                    content,
                    category=category,
                    importance=importance,
                    source_kind=source,
                    confidence=0.9 if source == "user_explicit" else 0.72,
                    tags=tags,
                )
            )

        episodic_ids = []
        for content, importance in [
            ("今天下午和用户在西湖边散步，聊到了记忆衰减和遗忘。", 0.6),
            ("晚上一起研究了图谱布局，用户更喜欢力导向视图。", 0.55),
            ("深夜用户提到最近工作压力大，森提醒他早点休息。", 0.68),
            ("用户在雨天分享了一段关于童年老家的回忆。", 0.72),
        ]:
            episodic_ids.append(
                create(
                    client,
                    content,
                    category="event",
                    memory_type="episodic",
                    source_kind="user_explicit",
                    importance=importance,
                    confidence=0.88,
                    tags=["对话", "事件"],
                    conversation="conv-demo",
                )
            )

        # Typed, weighted graph edges.
        def relate(source: str, target: str, relation: str, strength: float) -> None:
            post(
                client,
                "/relations",
                {
                    "source_id": source,
                    "target_id": target,
                    "relation_type": relation,
                    "strength": strength,
                    "confidence": 0.8,
                },
            )

        relate(memory_ids[0], character_ids[2], "supports", 0.86)
        relate(memory_ids[1], memory_ids[11], "causes", 0.72)
        relate(memory_ids[3], memory_ids[16], "supports", 0.8)
        relate(memory_ids[6], memory_ids[12], "related_to", 0.62)
        relate(memory_ids[4], memory_ids[16], "supports", 0.7)
        relate(memory_ids[2], memory_ids[11], "contradicts", 0.76)
        relate(character_ids[3], memory_ids[4], "related_to", 0.58)
        relate(character_ids[4], memory_ids[13], "related_to", 0.5)
        relate(memory_ids[15], memory_ids[16], "related_to", 0.66)
        relate(episodic_ids[0], memory_ids[0], "related_to", 0.55)
        relate(episodic_ids[1], memory_ids[16], "related_to", 0.48)
        relate(episodic_ids[3], memory_ids[2], "related_to", 0.52)

        # Consolidate the episodic group to exercise summaries in the demo.
        consolidation = request_json(
            client,
            "POST",
            "/maintenance/consolidate",
            headers=headers(),
            json={},
        )
        print(f"consolidation: {consolidation['data']}")

        # Add a manual evidence trail to one fact.
        post(
            client,
            f"/memories/{memory_ids[0]}/evidence",
            {
                "source_kind": "user_explicit",
                "conversation_id": "conv-demo",
                "message_id": "seed-m1",
                "role": "user",
                "quote": "我喜欢喝手冲咖啡，不加糖。",
                "reinforce": True,
            },
        )

        stats = request_json(client, "GET", "/stats", headers=headers())["data"]
        print(
            "seeded:",
            stats["total"],
            "memories,",
            stats["relations"],
            "relations,",
            stats["spaces"],
            "spaces",
        )
        print("yuki private memory:", yuki_id[:8])
    except SeedError as exc:
        print(f"\n生成演示数据失败：{exc}", file=sys.stderr)
        print("请确认服务已启动、令牌正确，然后重试。", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - developer helper must report clearly
        print(f"\n生成演示数据失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
        if service_process is not None and not args.keep_running:
            print("正在关闭自动启动的 MoriMemory 服务…")
            service_process.terminate()
            try:
                service_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                service_process.kill()
                service_process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
