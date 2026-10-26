import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands

from bot import commands as bot_commands


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class KanadeckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot_commands.setup(self.bot)
        self.command = self.bot.tree.get_command("kanadeck")
        self.owner = self.member(12, "Propriétaire")
        self.viewer = self.member(34, "Visiteur")
        self.rows = [(1, "Bulbakush", 50, 2, 20, "Commun", 2, datetime.now(timezone.utc))]
        self.sleep_patch = patch("bot.commands.asyncio.sleep", new_callable=AsyncMock)
        self.sleep_patch.start()
        self.image_patch = patch("bot.commands.os.path.exists", return_value=False)
        self.image_patch.start()

    async def asyncTearDown(self):
        self.image_patch.stop()
        self.sleep_patch.stop()
        await self.bot.close()

    @staticmethod
    def member(member_id, name, staff=False):
        member = Mock(spec=discord.Member)
        member.id = member_id
        member.display_name = name
        member.guild_permissions = discord.Permissions(manage_messages=staff)
        return member

    @staticmethod
    def interaction(member):
        return SimpleNamespace(
            user=member,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def open_deck(self, member, target=None, rows=None):
        interaction = self.interaction(member)
        cursor = SimpleNamespace(
            execute=AsyncMock(),
            fetchall=AsyncMock(return_value=self.rows if rows is None else rows),
            fetchone=AsyncMock(return_value=(31,)),
        )
        connection = SimpleNamespace(cursor=Mock(return_value=AsyncContext(cursor)))

        def acquire_after_defer():
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            return AsyncContext(connection)

        pool = SimpleNamespace(acquire=Mock(side_effect=acquire_after_defer))
        with patch.object(bot_commands.database, "db_pool", pool):
            await self.command.callback(interaction, target)
        self.assertEqual(cursor.execute.await_args_list[0].args[1], ((target or member).id,))
        interaction.response.send_message.assert_not_awaited()
        return interaction

    async def test_registration_replaces_pokedex(self):
        self.assertIsNotNone(self.command)
        self.assertIsNone(self.bot.tree.get_command("pokedex"))
        self.assertIn("Kanadeck", self.command.description)

    async def test_other_member_collection_can_be_browsed_without_sale_button(self):
        initial = await self.open_deck(self.viewer, self.owner)
        view = initial.followup.send.await_args.kwargs["view"]
        click = self.interaction(self.viewer)
        await view.children[0].callback(click)

        card = click.followup.send.await_args_list[0]
        self.assertEqual(card.kwargs["embed"].title, "Bulbakush 🌿")
        self.assertNotIn("view", card.kwargs)
        self.assertTrue(card.kwargs["ephemeral"])
        self.assertEqual(click.followup.send.await_count, 2)

    async def test_other_member_card_with_image_omits_view_instead_of_passing_none(self):
        initial = await self.open_deck(self.viewer, self.owner)
        view = initial.followup.send.await_args.kwargs["view"]
        click = self.interaction(self.viewer)
        file = Mock()
        with patch("bot.commands.os.path.exists", return_value=True), patch(
            "bot.commands.discord.File", return_value=file
        ):
            await view.children[0].callback(click)
        card = click.followup.send.await_args_list[0]
        self.assertIs(card.kwargs["file"], file)
        self.assertNotIn("view", card.kwargs)

    async def test_third_party_cannot_use_requester_menu_even_if_collection_owner(self):
        initial = await self.open_deck(self.viewer, self.owner)
        view = initial.followup.send.await_args.kwargs["view"]
        for third_party in (self.owner, self.member(56, "Tiers")):
            with self.subTest(member_id=third_party.id):
                click = self.interaction(third_party)
                await view.children[0].callback(click)
                click.followup.send.assert_awaited_once()
                reply = click.followup.send.await_args
                self.assertIn("autre utilisateur", reply.args[0])
                self.assertNotIn("embed", reply.kwargs)
                self.assertNotIn("view", reply.kwargs)

    async def test_owner_can_sell_from_own_collection(self):
        initial = await self.open_deck(self.owner)
        view = initial.followup.send.await_args.kwargs["view"]
        click = self.interaction(self.owner)
        await view.children[0].callback(click)
        sale = click.followup.send.await_args_list[0].kwargs["view"]
        self.assertIsInstance(sale, bot_commands.ClaimPokeweedView)
        self.assertEqual(sale.user_id, self.owner.id)
        self.assertEqual(sale.pokeweed_id, 1)

    async def test_navigation_preserves_requester_and_collection_owner(self):
        initial = await self.open_deck(self.viewer, self.owner)
        view = initial.followup.send.await_args.kwargs["view"]
        for _ in range(2):
            click = self.interaction(self.viewer)
            await view.children[0].callback(click)
            self.assertNotIn("view", click.followup.send.await_args_list[0].kwargs)
            view = click.followup.send.await_args.kwargs["view"]
            self.assertEqual(view.viewer_id, self.viewer.id)
            self.assertIs(view.owner, self.owner)
            self.assertTrue(all(button.viewer_id == self.viewer.id for button in view.children))

    async def test_empty_collection_uses_followup_after_deferring(self):
        interaction = await self.open_deck(self.viewer, self.owner, rows=[])
        interaction.followup.send.assert_awaited_once()
        self.assertIn("aucun Kanamon", interaction.followup.send.await_args.args[0])
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_staff_help_is_a_separate_private_message(self):
        command = self.bot.tree.get_command("help-commandes")
        interaction = self.interaction(self.member(12, "Staff", staff=True))
        await command.callback(interaction)
        public_help = interaction.response.send_message.await_args
        staff_help = interaction.followup.send.await_args
        self.assertLessEqual(len(public_help.args[0]), 2000)
        self.assertLessEqual(len(staff_help.args[0]), 2000)
        self.assertNotIn("/kanasay", public_help.args[0])
        for name in ("/kanasay", "/modifier-event", "/actualiser-events"):
            self.assertIn(name, staff_help.args[0])
        self.assertTrue(staff_help.kwargs["ephemeral"])

    async def test_member_help_does_not_include_staff_message(self):
        command = self.bot.tree.get_command("help-commandes")
        interaction = self.interaction(self.viewer)
        await command.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
