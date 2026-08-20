"""User account binding workflows for NovaBot."""

from __future__ import annotations


def bind_yuque_account(*, storage, platform_id: str, query: str) -> str:
    """Bind a chat platform user to a synced Yuque member."""

    existing = storage.get_binding(platform_id)
    if existing:
        return f"已绑定 @{existing['yuque_login']}\n使用 /unbind 解绑后重新绑定"

    if not query:
        return "请提供用户名:\n/bind <用户名>\n\n例如: /bind 张三"

    normalized_query = query.strip()
    if len(normalized_query) > 100:
        return "❌ 用户名过长（最多 100 字符）"

    members = storage.load_members()
    if not members:
        return "❌ 团队成员未同步\n请先执行 /sync members"

    matched = storage.find_member_by_name(normalized_query)
    if not matched:
        sample = [info.get("name", "") for info in list(members.values())[:5]]
        return f"❌ 未找到「{normalized_query}」\n成员示例: {', '.join(sample)}"

    storage.add_binding(
        platform_id,
        {
            "yuque_id": matched["id"],
            "yuque_login": matched.get("login", ""),
            "yuque_name": matched.get("name", ""),
        },
    )
    return (
        "✅ 绑定成功\n"
        "━━━━━━━━━━━━━━━\n"
        f"账号: @{matched.get('login', '')} ({matched.get('name', '')})\n"
        "\n"
        "💡 使用 /profile refresh 生成用户画像"
    )


def unbind_yuque_account(*, storage, platform_id: str) -> str:
    """Remove a chat platform user's Yuque binding."""

    binding = storage.get_binding(platform_id)
    if not binding:
        return "你还没有绑定账号"

    storage.remove_binding(platform_id)
    return f"✅ 已解除绑定 @{binding.get('yuque_login', '')}"
