"""测试应用命令"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSubscribeFeedCommand:
    """测试订阅 Feed 命令"""

    @pytest.mark.asyncio
    async def test_subscribe_new_feed(self):
        """测试订阅新 Feed"""
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription
        from astrbot_plugin_rsshub.src.infrastructure.config import FeedFetchSettings

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(
                id=1,
                user_id="user123",
                feed_id=1,
                target_session="test:Group:12345",
                platform_name="telegram",
            )
        )

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetch_settings=FeedFetchSettings(timeout=12, proxy="http://proxy.local"),
            fetcher_factory=fetcher_factory,
        )

        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            target_session="test:Group:12345",
            platform_name="telegram",
        )

        assert result.success is True
        assert result.data.feed_id == 1
        assert result.data.target_session == "test:Group:12345"
        fetcher_factory.assert_called_once_with(
            timeout=12,
            proxy="http://proxy.local",
        )
        fetcher.fetch.assert_awaited_once_with("https://example.com/rss.xml")
        fetcher.close.assert_awaited_once()
        feed_repo.save.assert_awaited_once()
        sub_repo.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subscribe_existing_feed(self):
        """测试订阅已存在的 Feed"""
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        existing_feed = Feed(id=1, link="https://example.com/rss.xml", title="Existing")

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Fetched Title"}),
        )
        fetcher.close = AsyncMock()
        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=existing_feed)
        feed_repo.save = AsyncMock()
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=MagicMock(return_value=fetcher),
        )

        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            target_session="test:Group:12345",
            platform_name="telegram",
        )

        assert result.success is True
        feed_repo.save.assert_not_called()
        sub_repo.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subscribe_same_feed_allowed_in_different_sessions(self):
        """回归 issue #70：同一用户在不同会话可对同一 Feed 各自订阅一份。

        查重必须按 (用户, Feed, 目标会话)；不同会话不应被误判为重复。
        """
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        existing_feed = Feed(id=1, link="https://example.com/rss.xml", title="Existing")
        existing_session = "test:Group:AAA"
        existing_sub = Subscription(
            id=1, user_id="user123", feed_id=1, target_session=existing_session
        )

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Fetched Title"}),
        )
        fetcher.close = AsyncMock()
        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=existing_feed)
        feed_repo.save = AsyncMock()

        async def _dedup(user_id, feed_id, target_session):
            # 仅当 (用户, Feed, 会话) 完全一致才算重复。
            if target_session == existing_session:
                return existing_sub
            return None

        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(side_effect=_dedup)
        sub_repo.save = AsyncMock(
            return_value=Subscription(
                id=2, user_id="user123", feed_id=1, target_session="test:Group:BBB"
            )
        )

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=MagicMock(return_value=fetcher),
        )

        # 不同会话：应成功新建订阅，而不是被「已经订阅」拦下。
        other = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            target_session="test:Group:BBB",
            platform_name="telegram",
        )
        assert other.success is True
        sub_repo.save.assert_awaited_once()

        # 相同会话：仍应按查重拒绝。
        same = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            target_session=existing_session,
            platform_name="telegram",
        )
        assert same.success is False
        assert "已经订阅" in same.message

    @pytest.mark.asyncio
    async def test_subscribe_invalid_url(self):
        """测试订阅无效 URL"""
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )

        feed_repo = MagicMock()
        sub_repo = MagicMock()
        fetcher_factory = MagicMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=fetcher_factory,
        )

        result = await cmd.execute(
            url="not-a-url",
            user_id="user123",
        )

        assert result.success is False
        assert "http" in result.message.lower()
        fetcher_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscribe_fetch_status_error_preserves_http_status(self):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.exceptions import WebError

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=WebError(
                error_name="status error",
                url="https://rsshub.app/pixiv/search/%E7%A2%A7%E8%93%9D%E6%A1%A3%E6%A1%88",
                status="404 Not Found",
            ),
            rss_d=None,
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        sub_repo = MagicMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=fetcher_factory,
        )

        result = await cmd.execute(
            url="https://rsshub.app/pixiv/search/%E7%A2%A7%E8%93%9D%E6%A1%A3%E6%A1%88",
            user_id="user123",
        )

        assert result.success is False
        assert result.message == (
            "订阅失败：status error (404 Not Found) | "
            "url=https://rsshub.app/pixiv/search/%E7%A2%A7%E8%93%9D%E6%A1%A3%E6%A1%88"
        )
        fetcher.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_subscribe_applies_session_default_handlers_mode(self):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )
        sub_repo.update_options = AsyncMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=fetcher_factory,
        )

        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            session_defaults={"handlers_mode": "disabled"},
        )

        assert result.success is True
        sub_repo.update_options.assert_awaited_once_with(
            1,
            "user123",
            handlers_mode="disabled",
        )

    @pytest.mark.asyncio
    async def test_subscribe_applies_default_handlers(self):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )
        sub_repo.update_options = AsyncMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=fetcher_factory,
        )

        comment_handler = {
            "id": "builtin.ai_comment.default",
            "type": "builtin",
            "name": "ai_comment",
            "status": 1,
            "config": {"prompt": "吐槽一下", "with_media": True},
        }
        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            default_handlers=[comment_handler],
        )

        assert result.success is True
        sub_repo.update_options.assert_awaited_once_with(
            1,
            "user123",
            handlers=[comment_handler],
        )

    @pytest.mark.asyncio
    async def test_subscribe_session_handlers_override_default_handlers(self):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )
        sub_repo.update_options = AsyncMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=fetcher_factory,
        )

        comment_handler = {
            "id": "builtin.ai_comment.default",
            "type": "builtin",
            "name": "ai_comment",
            "status": 1,
            "config": {"prompt": "吐槽一下", "with_media": True},
        }
        session_handlers = [
            {
                "id": "builtin.ai_filter.default",
                "type": "builtin",
                "name": "ai_filter",
                "status": 1,
                "config": {"prompt": "keep important"},
            }
        ]
        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            session_defaults={"handlers": session_handlers},
            default_handlers=[comment_handler],
        )

        assert result.success is True
        # session_defaults 显式 handlers 优先，default_handlers 不重复写
        sub_repo.update_options.assert_awaited_once()
        handlers_written = sub_repo.update_options.await_args.kwargs["handlers"]
        assert [h["name"] for h in handlers_written] == ["ai_filter"]

    @pytest.mark.asyncio
    async def test_subscribe_ignores_invalid_session_default_interval_below_minimal(
        self, monkeypatch
    ):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription
        from astrbot_plugin_rsshub.src.infrastructure.config import (
            FeedFetchSettings,
            RsshubPluginConfig,
            config_loader,
        )

        monkeypatch.setattr(
            config_loader,
            "_config",
            RsshubPluginConfig.from_astrbot_config(
                {"basic_config": {"minimal_interval": 5}}
            ),
        )

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        fetcher_factory = MagicMock(return_value=fetcher)

        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )
        sub_repo.update_options = AsyncMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetch_settings=FeedFetchSettings(),
            fetcher_factory=fetcher_factory,
        )

        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id="user123",
            session_defaults={"interval": 4},
        )

        assert result.success is True
        sub_repo.update_options.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subscribe_ensures_user_before_saving_subscription(self):
        from astrbot_plugin_rsshub.src.application.commands.subscribe_feed_cmd import (
            SubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        fetcher = AsyncMock()
        fetcher.fetch.return_value = MagicMock(
            error=None,
            rss_d=MagicMock(feed={"title": "Test Feed"}),
        )
        fetcher.close = AsyncMock()
        feed_repo = MagicMock()
        feed_repo.get_by_link = AsyncMock(return_value=None)
        feed_repo.save = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )
        sub_repo = MagicMock()
        sub_repo.get_by_user_feed_session = AsyncMock(return_value=None)
        sub_repo.save = AsyncMock(
            return_value=Subscription(id=1, user_id="user123", feed_id=1)
        )
        user_repo = MagicMock()
        user_repo.get_or_create = AsyncMock()

        cmd = SubscribeFeedCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            fetcher_factory=MagicMock(return_value=fetcher),
            user_repo=user_repo,
        )

        result = await cmd.execute(
            url="https://example.com/rss.xml",
            user_id=" user123 ",
        )

        assert result.success is True
        user_repo.get_or_create.assert_awaited_once_with("user123")
        sub_repo.get_by_user_feed_session.assert_awaited_once_with("user123", 1, None)


class TestUnsubscribeFeedCommand:
    """测试取消订阅命令"""

    @pytest.mark.asyncio
    async def test_unsubscribe_success(self):
        """测试成功取消订阅"""
        from astrbot_plugin_rsshub.src.application.commands.unsubscribe_feed_cmd import (
            UnsubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        sub_repo = MagicMock()
        subscription = Subscription(
            id=1,
            user_id="user123",
            feed_id=1,
        )
        sub_repo.get_by_id = AsyncMock(return_value=subscription)
        sub_repo.delete = AsyncMock()

        feed_repo = MagicMock()
        feed_repo.get_by_id = AsyncMock(
            return_value=Feed(
                id=1, link="https://example.com/rss.xml", title="Test Feed"
            )
        )

        cmd = UnsubscribeFeedCommand(sub_repo, feed_repo)

        result = await cmd.execute(
            sub_id=1,
            user_id="user123",
        )

        assert result.success is True
        assert "Test Feed" in result.message
        sub_repo.delete.assert_awaited_once_with(subscription)

    @pytest.mark.asyncio
    async def test_unsubscribe_not_found(self):
        """测试取消不存在的订阅"""
        from astrbot_plugin_rsshub.src.application.commands.unsubscribe_feed_cmd import (
            UnsubscribeFeedCommand,
        )

        sub_repo = MagicMock()
        sub_repo.get_by_id = AsyncMock(return_value=None)

        feed_repo = MagicMock()

        cmd = UnsubscribeFeedCommand(sub_repo, feed_repo)

        result = await cmd.execute(
            sub_id=999,
            user_id="user123",
        )

        assert result.success is False
        assert "不存在" in result.message

    @pytest.mark.asyncio
    async def test_unsubscribe_permission_denied(self):
        """测试无权限取消订阅"""
        from astrbot_plugin_rsshub.src.application.commands.unsubscribe_feed_cmd import (
            UnsubscribeFeedCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        sub_repo = MagicMock()
        sub_repo.get_by_id = AsyncMock(
            return_value=Subscription(
                id=1,
                user_id="other_user",  # 不同用户
                feed_id=1,
                target_session="other:Group:1",
            )
        )

        feed_repo = MagicMock()

        cmd = UnsubscribeFeedCommand(sub_repo, feed_repo)

        result = await cmd.execute(
            sub_id=1,
            user_id="user123",
            current_session="test:Group:12345",
        )

        assert result.success is False
        assert "无权限" in result.message


class TestTestSubscriptionCommand:
    """测试订阅测试命令"""

    @pytest.mark.asyncio
    async def test_test_subscription_uses_polling_service_read_path(self):
        from astrbot_plugin_rsshub.src.application.commands.test_subscription_cmd import (
            TestSubscriptionCommand,
        )
        from astrbot_plugin_rsshub.src.application.services.feed_polling_service import (
            FeedReadResult,
        )
        from astrbot_plugin_rsshub.src.domain.entities.feed import Feed
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription
        from astrbot_plugin_rsshub.src.infrastructure.fetcher.rss.parser import (
            EntryParsed,
        )

        subscription = Subscription(id=5, user_id="user123", feed_id=1)
        feed = Feed(id=1, link="https://example.com/rss.xml", title="Example")
        entry = EntryParsed(
            guid="guid-1",
            title="Entry",
            link="https://example.com/entry",
            summary="Summary",
        )
        web_feed = MagicMock(rss_d=MagicMock(feed={"title": "Example"}))

        sub_repo = MagicMock()
        sub_repo.get_by_id = AsyncMock(return_value=subscription)
        feed_repo = MagicMock()
        feed_repo.get_by_id = AsyncMock(return_value=feed)
        polling_service = AsyncMock()
        polling_service.fetch_feed_entries.return_value = FeedReadResult(
            success=True,
            status="fetched",
            message="ok",
            entries=[entry],
            web_feed=web_feed,
        )

        cmd = TestSubscriptionCommand(
            subscription_repo=sub_repo,
            feed_repo=feed_repo,
            polling_service=polling_service,
        )

        result = await cmd.execute(sub_id=5, user_id="user123")

        assert result.success is True
        assert result.data["test_result"].entry_count == 1
        polling_service.fetch_feed_entries.assert_awaited_once_with(
            "https://example.com/rss.xml",
            verbose=True,
        )

    @pytest.mark.asyncio
    async def test_test_url_uses_polling_service_read_path(self):
        from astrbot_plugin_rsshub.src.application.commands.test_subscription_cmd import (
            TestSubscriptionCommand,
        )
        from astrbot_plugin_rsshub.src.application.services.feed_polling_service import (
            FeedReadResult,
        )
        from astrbot_plugin_rsshub.src.infrastructure.fetcher.rss.parser import (
            EntryParsed,
        )

        entry = EntryParsed(
            guid="guid-1",
            title="Entry",
            link="https://example.com/entry",
            summary="Summary",
        )
        web_feed = MagicMock(rss_d=MagicMock(feed={"title": "Example"}))
        polling_service = AsyncMock()
        polling_service.fetch_feed_entries.return_value = FeedReadResult(
            success=True,
            status="fetched",
            message="ok",
            entries=[entry],
            web_feed=web_feed,
        )

        cmd = TestSubscriptionCommand(
            subscription_repo=MagicMock(),
            feed_repo=MagicMock(),
            polling_service=polling_service,
        )

        result = await cmd.execute_by_url("https://example.com/rss.xml")

        assert result.success is True
        assert result.data["test_result"].feed_info.title == "Example"
        polling_service.fetch_feed_entries.assert_awaited_once_with(
            "https://example.com/rss.xml",
            verbose=True,
        )


class TestUpdateSubscriptionMergeCondition:
    """测试「合并转发条件」开关的 reconcile 逻辑"""

    @staticmethod
    def _merge_handler(max_chars=80, max_images=1, handler_id="builtin.merge_condition.default", status=1):
        return {
            "id": handler_id,
            "type": "builtin",
            "name": "merge_condition",
            "status": status,
            "config": {"max_chars": max_chars, "max_images": max_images},
        }

    def test_reconcile_disable_removes_merge_condition(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            _reconcile_merge_condition_handlers,
        )

        base = [
            self._merge_handler(),
            {"id": "x", "type": "builtin", "name": "ai_filter", "status": 1, "config": {}},
        ]
        out = _reconcile_merge_condition_handlers(
            base,
            enabled=False,
            max_chars=None,
            max_images=None,
            user_handlers=[],
            mode="override",
        )
        assert [h["name"] for h in out] == ["ai_filter"]

    def test_reconcile_enable_empty_inherit_snapshots_user_handlers(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            _reconcile_merge_condition_handlers,
        )

        user_handlers = [
            {"id": "u1", "type": "builtin", "name": "ai_filter", "status": 1, "config": {}}
        ]
        out = _reconcile_merge_condition_handlers(
            [],
            enabled=True,
            max_chars=50,
            max_images=2,
            user_handlers=user_handlers,
            mode="inherit",
        )
        assert [h["name"] for h in out] == ["ai_filter", "merge_condition"]
        assert out[-1]["status"] == 1
        assert out[-1]["config"] == {"max_chars": 50, "max_images": 2}

    def test_reconcile_enable_override_does_not_snapshot(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            _reconcile_merge_condition_handlers,
        )

        out = _reconcile_merge_condition_handlers(
            [],
            enabled=True,
            max_chars=100,
            max_images=3,
            user_handlers=[{"id": "u1", "name": "ai_filter", "status": 1, "config": {}}],
            mode="override",
        )
        assert [h["name"] for h in out] == ["merge_condition"]
        assert out[0]["config"] == {"max_chars": 100, "max_images": 3}

    def test_reconcile_enable_existing_upsert_preserves_id_and_omitted_field(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            _reconcile_merge_condition_handlers,
        )

        existing = self._merge_handler(handler_id="builtin.merge_condition.custom")
        out = _reconcile_merge_condition_handlers(
            [existing],
            enabled=True,
            max_chars=120,
            max_images=None,
            user_handlers=[],
            mode="override",
        )
        assert len(out) == 1
        assert out[0]["id"] == "builtin.merge_condition.custom"
        # max_images 未传，保留原值 1
        assert out[0]["config"] == {"max_chars": 120, "max_images": 1}

    def test_reconcile_enable_none_thresholds_use_defaults(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            _reconcile_merge_condition_handlers,
        )

        out = _reconcile_merge_condition_handlers(
            [],
            enabled=True,
            max_chars=None,
            max_images=None,
            user_handlers=[],
            mode="override",
        )
        assert out[0]["config"] == {"max_chars": 80, "max_images": 1}

    @pytest.mark.asyncio
    async def test_execute_merge_condition_spec_reconciles_handlers(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            UpdateSubscriptionCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        sub = Subscription(id=1, user_id="user123", feed_id=1, handlers_mode="override")
        sub_repo = MagicMock()
        sub_repo.get_by_id = AsyncMock(return_value=sub)
        sub_repo.update_options = AsyncMock(return_value=sub)

        cmd = UpdateSubscriptionCommand(
            subscription_repo=sub_repo,
            get_user_settings_cmd=None,
        )
        result = await cmd.execute(
            sub_id=1,
            user_id="user123",
            handlers_mode="override",
            merge_condition={"enabled": True, "max_chars": 42, "max_images": 2},
        )

        assert result.success is True
        handlers = sub_repo.update_options.await_args.kwargs["handlers"]
        assert [h["name"] for h in handlers] == ["merge_condition"]
        assert handlers[0]["config"] == {"max_chars": 42, "max_images": 2}

    @pytest.mark.asyncio
    async def test_execute_merge_condition_disable_removes_handler(self):
        from astrbot_plugin_rsshub.src.application.commands.update_subscription_cmd import (
            UpdateSubscriptionCommand,
        )
        from astrbot_plugin_rsshub.src.domain.entities.subscription import Subscription

        sub = Subscription(
            id=1,
            user_id="user123",
            feed_id=1,
            handlers_mode="override",
            handlers=[
                self._merge_handler(),
                {
                    "id": "builtin.ai_filter.default",
                    "type": "builtin",
                    "name": "ai_filter",
                    "status": 1,
                    "config": {"prompt": "keep important"},
                },
            ],
        )
        sub_repo = MagicMock()
        sub_repo.get_by_id = AsyncMock(return_value=sub)
        sub_repo.update_options = AsyncMock(return_value=sub)

        cmd = UpdateSubscriptionCommand(
            subscription_repo=sub_repo,
            get_user_settings_cmd=None,
        )
        result = await cmd.execute(
            sub_id=1,
            user_id="user123",
            handlers_mode="override",
            merge_condition={"enabled": False},
        )

        assert result.success is True
        handlers = sub_repo.update_options.await_args.kwargs["handlers"]
        assert [h["name"] for h in handlers] == ["ai_filter"]
