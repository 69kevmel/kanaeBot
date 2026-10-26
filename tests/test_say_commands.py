import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands
from discord.ext import commands

from bot import say_commands


class KanaSayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        say_commands.setup(self.bot)
        self.command = self.bot.tree.get_command("kanasay")
        self.member = Mock(spec=discord.Member)
        self.member.id = 12
        self.member.guild_permissions = discord.Permissions(manage_messages=True)
        self.bot_member = Mock(spec=discord.Member)
        self.guild = SimpleNamespace(id=1, me=self.bot_member)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 34
        self.channel.guild = self.guild
        self.user_permissions = discord.Permissions(view_channel=True, send_messages=True, manage_messages=True)
        self.bot_permissions = discord.Permissions(view_channel=True, send_messages=True)
        self.channel.permissions_for.side_effect = lambda member: (
            self.user_permissions if member is self.member else self.bot_permissions
        )
        self.channel.send = AsyncMock(return_value=SimpleNamespace(
            id=56, jump_url="https://discord.com/channels/1/34/56"
        ))
        self.interaction = SimpleNamespace(
            guild=self.guild, guild_id=1, user=self.member,
            created_at=discord.utils.utcnow(),
            response=SimpleNamespace(
                send_message=AsyncMock(), defer=AsyncMock(), is_done=Mock(return_value=False)
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def asyncTearDown(self):
        await self.bot.close()

    async def invoke(self, content="Bonjour la commu !"):
        await self.command.callback(self.interaction, self.channel, content)

    def assert_no_publication(self):
        self.channel.send.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()
        self.assertTrue(self.interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_registration_limits_and_staff_default(self):
        self.assertTrue(self.command.guild_only)
        self.assertTrue(self.command.default_permissions.manage_messages)
        payload = self.command.to_dict(self.bot.tree)
        options = {option["name"]: option for option in payload["options"]}
        self.assertEqual(options["message"]["min_length"], 1)
        self.assertEqual(options["message"]["max_length"], 2000)
        self.assertIn(discord.ChannelType.text.value, options["salon"]["channel_types"])

    async def test_publish_preserves_multiline_and_disables_every_mention(self):
        content = "Annonce : @everyone <@12> <@&90>\nDeuxième ligne 🌿"
        await self.invoke(content)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        args = self.channel.send.await_args
        self.assertEqual(args.args, (content,))
        mentions = args.kwargs["allowed_mentions"].to_dict()
        self.assertEqual(mentions["parse"], [])
        self.assertFalse(args.kwargs["allowed_mentions"].replied_user)
        followup = self.interaction.followup.send.await_args
        self.assertTrue(followup.kwargs["ephemeral"])
        self.assertIn("https://discord.com/channels/1/34/56", followup.args[0])

    async def test_runtime_permission_still_required_if_slash_permissions_overridden(self):
        self.member.guild_permissions = discord.Permissions.none()
        await self.invoke()
        self.assert_no_publication()

    async def test_user_must_be_able_to_view_send_and_moderate_target(self):
        for permission in ("view_channel", "send_messages", "manage_messages"):
            with self.subTest(permission=permission):
                self.user_permissions = discord.Permissions(view_channel=True, send_messages=True, manage_messages=True)
                setattr(self.user_permissions, permission, False)
                self.interaction.response.send_message.reset_mock()
                await self.invoke()
                self.assert_no_publication()

    async def test_bot_must_be_able_to_view_and_send_in_target(self):
        for permission in ("view_channel", "send_messages"):
            with self.subTest(permission=permission):
                self.bot_permissions = discord.Permissions(view_channel=True, send_messages=True)
                setattr(self.bot_permissions, permission, False)
                self.interaction.response.send_message.reset_mock()
                await self.invoke()
                self.assert_no_publication()

    async def test_missing_bot_member(self):
        self.guild.me = None
        await self.invoke()
        self.assert_no_publication()

    async def test_foreign_guild(self):
        self.channel.guild = SimpleNamespace(id=2)
        await self.invoke()
        self.assert_no_publication()

    async def test_threads_and_forums_are_not_text_targets(self):
        for channel_type in (discord.Thread, discord.ForumChannel):
            with self.subTest(channel_type=channel_type):
                self.channel = Mock(spec=channel_type)
                self.channel.send = AsyncMock()
                self.interaction.response.send_message.reset_mock()
                await self.invoke()
                self.assert_no_publication()

    async def test_direct_message_is_rejected(self):
        self.interaction.guild = None
        await self.invoke()
        self.assert_no_publication()

    async def test_empty_and_oversized_inputs_do_not_publish(self):
        for content in ("", " \n\t ", "x" * 2001):
            with self.subTest(length=len(content)):
                self.interaction.response.send_message.reset_mock()
                await self.invoke(content)
                self.assert_no_publication()

    async def test_exact_message_limit_is_accepted(self):
        await self.invoke("x" * 2000)
        self.channel.send.assert_awaited_once()

    async def test_discord_failures_have_private_errors_and_no_success(self):
        for error_type, status in ((discord.Forbidden, 403), (discord.NotFound, 404), (discord.HTTPException, 500)):
            with self.subTest(error_type=error_type):
                self.channel.send.side_effect = error_type(SimpleNamespace(status=status, reason="test"), "test")
                self.interaction.followup.send.reset_mock()
                with self.assertLogs(say_commands.logger, level="ERROR") if status == 500 else nullcontext():
                    await self.invoke()
                followup = self.interaction.followup.send.await_args
                self.assertTrue(followup.kwargs["ephemeral"])
                self.assertTrue(followup.args[0].startswith("❌"))

    async def test_cooldown_blocks_repeat_invocation_and_is_per_member(self):
        check = self.command.checks[0]
        self.assertTrue(await check(self.interaction))
        with self.assertRaises(app_commands.CommandOnCooldown):
            await check(self.interaction)
        self.member.id = 13
        self.assertTrue(await check(self.interaction))

    async def test_cooldown_error_is_private(self):
        error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 5), 4.5)
        await self.command.on_error(self.interaction, error)
        self.assert_no_publication()
        self.assertIn("4.5", self.interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
