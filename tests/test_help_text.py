from novabot.help_text import format_help_text


def test_help_text_exposes_multi_team_sync_without_dropping_community_commands():
    text = format_help_text()

    assert "/sync - 同步全部已启用团队知识库" in text
    assert "/sync <team_id> - 只同步指定团队" in text
    assert "/sync team <team_id> - 只同步指定团队" in text
    assert "/sync members - 同步全部已启用团队成员" in text
    assert "/sync members <team_id> - 同步指定团队成员" in text

    assert "/profile refresh - 刷新画像" in text
    assert "/subscribe repo <知识库> - 订阅知识库" in text
    assert "/partner <主题> - 按主题推荐" in text
    assert "/collab find <主题> - 寻找协作伙伴" in text
