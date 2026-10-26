import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from bot import database, event_commands, helpers


def interaction_for(owner=42, admin=False, staff=True):
    permissions = SimpleNamespace(administrator=admin, manage_messages=staff)
    bot_permissions = SimpleNamespace(manage_events=True, create_events=True)
    guild = SimpleNamespace(id=100, me=SimpleNamespace(guild_permissions=bot_permissions), fetch_scheduled_event=AsyncMock())
    return SimpleNamespace(
        guild=guild, guild_id=100,
        user=SimpleNamespace(id=owner, guild_permissions=permissions),
        client=SimpleNamespace(user=SimpleNamespace(id=999), get_channel=MagicMock(), fetch_channel=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=MagicMock(return_value=True)),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class EventEditingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.interaction = interaction_for()
        self.event = SimpleNamespace(
            id=77, name="Avant", description="Description originale", creator_id=999,
            status=discord.EventStatus.scheduled, edit=AsyncMock(),
        )
        self.interaction.guild.fetch_scheduled_event.return_value = self.event
        self.slot = (date(2026, 10, 26), "21h", True, 42, "Avant", "Description originale", 77)
        self.cursor = object()

        @asynccontextmanager
        async def locked_slot(*args):
            yield self.slot, self.cursor

        self.lock_patch = patch.object(database, "locked_pro_slot", locked_slot)
        self.update_patch = patch.object(database, "update_pro_slot_content", new_callable=AsyncMock)
        self.lock_patch.start()
        self.update = self.update_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.addCleanup(self.update_patch.stop)

    async def test_edits_only_title_and_preserves_other_fields(self):
        self.assertTrue(await event_commands.edit_planned_event(self.interaction, 5, titre="Nouveau"))
        self.event.edit.assert_awaited_once_with(name="Nouveau", reason="/modifier-event par 42")
        self.update.assert_awaited_once_with(self.cursor, 5, "Nouveau", "Description originale")

    async def test_full_length_description_is_not_truncated(self):
        description = "a" * 1000
        await event_commands.edit_planned_event(self.interaction, 5, description=description)
        self.assertEqual(self.event.edit.await_args.kwargs["description"], description)
        self.update.assert_awaited_once_with(self.cursor, 5, "Avant", description)

    async def test_owner_is_checked_for_hand_typed_slot_ids(self):
        self.interaction.user.id = 43
        with self.assertRaises(PermissionError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.interaction.guild.fetch_scheduled_event.assert_not_awaited()
        self.update.assert_not_awaited()

    async def test_admin_can_edit_another_owners_event(self):
        self.interaction.user.id = 43
        self.interaction.user.guild_permissions.administrator = True
        self.assertTrue(await event_commands.edit_planned_event(self.interaction, 5, titre="Oui"))

    async def test_unreserved_slot_cannot_be_edited(self):
        self.slot = (*self.slot[:2], False, *self.slot[3:])
        with self.assertRaises(ValueError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.update.assert_not_awaited()

    async def test_native_failure_prevents_db_update(self):
        self.event.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Denied")
        with self.assertRaises(discord.Forbidden):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.update.assert_not_awaited()

    async def test_db_failure_restores_native_content(self):
        self.update.side_effect = RuntimeError("DB unavailable")
        with self.assertRaises(RuntimeError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Nouveau")
        self.assertEqual(self.event.edit.await_count, 2)
        self.assertEqual(self.event.edit.await_args.kwargs["name"], "Avant")

    async def test_commit_failure_also_restores_native_content(self):
        @asynccontextmanager
        async def failed_commit(*args):
            yield self.slot, self.cursor
            raise RuntimeError("Commit failed")

        with patch.object(database, "locked_pro_slot", failed_commit):
            with self.assertRaises(RuntimeError):
                await event_commands.edit_planned_event(self.interaction, 5, titre="Nouveau")
        self.assertEqual(self.event.edit.await_count, 2)
        self.assertEqual(self.event.edit.await_args.kwargs["name"], "Avant")

    async def test_compensation_finishes_before_another_edit_starts(self):
        restore_started = asyncio.Event()
        allow_restore = asyncio.Event()

        async def edit_native(**kwargs):
            if kwargs.get("name") == "Avant":
                restore_started.set()
                await allow_restore.wait()

        self.event.edit.side_effect = edit_native
        self.update.side_effect = [RuntimeError("DB unavailable"), None]
        first = asyncio.create_task(event_commands.edit_planned_event(self.interaction, 5, titre="Première"))
        await restore_started.wait()
        second = asyncio.create_task(event_commands.edit_planned_event(self.interaction, 5, titre="Deuxième"))
        await asyncio.sleep(0)
        self.assertEqual(self.event.edit.await_count, 2)
        allow_restore.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], RuntimeError)
        self.assertTrue(results[1])
        self.assertEqual(self.event.edit.await_args.kwargs["name"], "Deuxième")

    async def test_no_content_does_not_edit_native_event(self):
        self.assertFalse(await event_commands.edit_planned_event(self.interaction, 5))
        self.event.edit.assert_not_awaited()
        self.update.assert_not_awaited()

    async def test_missing_native_id_does_not_change_reservation(self):
        self.slot = (*self.slot[:-1], None)
        with self.assertRaises(ValueError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.update.assert_not_awaited()

    async def test_completed_event_is_refused(self):
        self.event.status = discord.EventStatus.completed
        with self.assertRaises(ValueError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.event.edit.assert_not_awaited()

    async def test_bot_can_edit_own_event_with_create_events(self):
        self.interaction.guild.me.guild_permissions.manage_events = False
        self.assertTrue(await event_commands.edit_planned_event(self.interaction, 5, titre="Oui"))

    async def test_create_events_does_not_allow_editing_someone_elses_event(self):
        self.interaction.guild.me.guild_permissions.manage_events = False
        self.event.creator_id = 123
        with self.assertRaises(PermissionError):
            await event_commands.edit_planned_event(self.interaction, 5, titre="Non")
        self.event.edit.assert_not_awaited()

    async def test_input_limits_and_blank_content_are_validated(self):
        for title, description in [("a" * 101, None), ("   ", None), (None, "a" * 1001), (None, "\n ")]:
            with self.subTest(title=title, description=description):
                with self.assertRaises(ValueError):
                    await event_commands.edit_planned_event(self.interaction, 5, title, description)
        self.event.edit.assert_not_awaited()


class EventCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        event_commands.setup(self.bot)

    async def test_callback_rejects_non_staff_at_runtime(self):
        interaction = interaction_for(staff=False)
        with patch.object(database, "locked_pro_slot") as lock:
            await self.bot.tree.get_command("modifier-event").callback(interaction, "5", "Test")
        lock.assert_not_called()
        self.assertIn("staff", interaction.followup.send.await_args.args[0])

    async def test_callback_rejects_foreign_server_before_database_lookup(self):
        interaction = interaction_for()
        interaction.client.get_channel.return_value = SimpleNamespace(guild=SimpleNamespace(id=101))
        with patch.object(database, "locked_pro_slot") as lock:
            await self.bot.tree.get_command("modifier-event").callback(interaction, "5", "Test")
        lock.assert_not_called()
        self.assertIn("autre serveur", interaction.followup.send.await_args.args[0])

    async def test_autocomplete_bounds_and_owner_filter(self):
        interaction = interaction_for()
        interaction.client.get_channel.return_value = SimpleNamespace(guild=interaction.guild)
        rows = [(i, date(2026, 10, 26), "21h", "a" * 100) for i in range(40)]
        command = self.bot.tree.get_command("modifier-event")
        autocomplete = command._params["creneau"].autocomplete
        with patch.object(database, "get_reserved_pro_slots", AsyncMock(return_value=rows)) as fetch:
            choices = await autocomplete(interaction, "")
        self.assertEqual(len(choices), 25)
        self.assertTrue(all(len(choice.name) <= 100 for choice in choices))
        fetch.assert_awaited_once_with(database.db_pool, animateur_id=42)

    async def test_panel_failure_reports_event_as_saved(self):
        interaction = interaction_for()
        interaction.client.get_channel.return_value = SimpleNamespace(guild=interaction.guild)
        with patch.object(event_commands, "edit_planned_event", AsyncMock(return_value=True)), patch.object(helpers, "refresh_event_message", AsyncMock(side_effect=RuntimeError("panel offline"))):
            await self.bot.tree.get_command("modifier-event").callback(interaction, "5", "Test")
        self.assertIn("a été modifié", interaction.followup.send.await_args.args[0])
        self.assertIn("actualiser-events", interaction.followup.send.await_args.args[0])

    async def test_slow_database_is_cancelled_before_autocomplete_deadline(self):
        interaction = interaction_for()
        interaction.client.get_channel.return_value = SimpleNamespace(guild=interaction.guild)
        autocomplete = self.bot.tree.get_command("modifier-event")._params["creneau"].autocomplete
        cancelled = asyncio.Event()

        async def stalled_fetch(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with patch.object(database, "get_reserved_pro_slots", stalled_fetch):
            with self.assertLogs(event_commands.logger, level="WARNING"):
                self.assertEqual(await autocomplete(interaction, ""), [])
        self.assertTrue(cancelled.is_set())


class PanelFormattingTests(unittest.TestCase):
    def test_all_embeds_share_a_single_6000_character_budget(self):
        now = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
        bot = SimpleNamespace(user=None)
        guild = SimpleNamespace(id=100)
        rows = [(now.date() + timedelta(days=i % 15), "21h30", 42, "a" * 100, "b" * 1000, i + 1) for i in range(500)]
        embeds = helpers.build_event_embeds(bot, guild, rows, [], now=now)
        self.assertLessEqual(sum(len(embed) for embed in embeds), 6000)
        self.assertLessEqual(len(embeds), 10)
        for embed in embeds:
            self.assertLessEqual(len(embed.fields), 25)
            for field in embed.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)
        self.assertTrue(any("Suite du planning" == field.name[2:].strip() or "Suite du planning" in field.name for field in embeds[0].fields))

    def test_native_event_appears_once_and_invalid_times_do_not_break_panel(self):
        now = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
        rows = [(now.date(), "25h99", 42, "Invalide", "", 1), (now.date(), "21h", 42, "Unique", "", 2)]
        native = SimpleNamespace(id=2, name="Doublon", description="", creator_id=42, start_time=now, status=discord.EventStatus.scheduled)
        embeds = helpers.build_event_embeds(SimpleNamespace(user=None), SimpleNamespace(id=100), rows, [native], now=now)
        values = "\n".join(str(embed.to_dict()) for embed in embeds)
        self.assertIn("UNIQUE", values)
        self.assertNotIn("DOUBLON", values)
        self.assertNotIn("INVALIDE", values)

    def test_invalid_database_time_falls_back_to_the_linked_native_event(self):
        now = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
        rows = [(now.date(), "21h30 heure de Paris", 42, "Ancien horaire", "", 2)]
        native = SimpleNamespace(id=2, name="Horaire natif valide", description="", creator_id=42, start_time=now, status=discord.EventStatus.scheduled)
        embeds = helpers.build_event_embeds(SimpleNamespace(user=None), SimpleNamespace(id=100), rows, [native], now=now)
        values = "\n".join(str(embed.to_dict()) for embed in embeds)
        self.assertIn("HORAIRE NATIF VALIDE", values)


class PanelRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        helpers._event_panel_lock = None
        helpers._event_panel_overrides.clear()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 123
        self.channel.guild = SimpleNamespace(id=100, me=object(), fetch_scheduled_events=AsyncMock(return_value=[]))
        self.channel.permissions_for.return_value = discord.Permissions.all()
        self.message = SimpleNamespace(id=456, author=SimpleNamespace(id=999), edit=AsyncMock())
        self.channel.send = AsyncMock(return_value=self.message)
        self.channel.fetch_message = AsyncMock()
        self.bot = SimpleNamespace(get_channel=MagicMock(return_value=self.channel), user=SimpleNamespace(id=999, display_avatar=SimpleNamespace(url="https://example.com/avatar.png")))
        self.patches = [
            patch.object(helpers.config, "EVENT_CHANNEL_ID", 123),
            patch.object(helpers.config, "EVENT_MESSAGE_ID", 111),
            patch.object(database, "get_event_panel_message_id", AsyncMock(return_value=111)),
            patch.object(database, "save_event_panel_message_id", AsyncMock()),
            patch.object(database, "get_public_events", AsyncMock(return_value=[])),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_deleted_panel_is_recreated_once_and_persisted(self):
        missing = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")
        self.channel.fetch_message.side_effect = [missing, self.message]
        self.assertIs(await helpers.refresh_event_message(self.bot), self.message)
        self.assertIs(await helpers.refresh_event_message(self.bot), self.message)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.fetch_message.await_args.args[0], 456)
        database.save_event_panel_message_id.assert_awaited_with(database.db_pool, 123, 111, 456)
        self.assertEqual(self.channel.send.await_args.kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_native_fetch_failure_preserves_existing_panel(self):
        self.channel.fetch_message.return_value = self.message
        self.channel.guild.fetch_scheduled_events.side_effect = RuntimeError("Discord unavailable")
        with self.assertRaises(RuntimeError):
            await helpers.refresh_event_message(self.bot)
        self.message.edit.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_persistence_failure_does_not_create_another_message_on_retry(self):
        missing = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")
        self.channel.fetch_message.side_effect = [missing, self.message]
        database.save_event_panel_message_id.side_effect = [RuntimeError("DB unavailable"), None]
        with self.assertRaises(RuntimeError):
            await helpers.refresh_event_message(self.bot)
        await helpers.refresh_event_message(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.fetch_message.await_args.args[0], self.message.id)


class ReservationTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_transaction_locks_row_and_rolls_back_on_failure(self):
        pool = MagicMock()
        conn = MagicMock()
        cursor = AsyncMock()
        conn.begin = AsyncMock()
        conn.commit = AsyncMock()
        conn.rollback = AsyncMock()
        pool.acquire.return_value.__aenter__.return_value = conn
        conn.cursor.return_value.__aenter__.return_value = cursor
        cursor.fetchone.return_value = ("reservation",)
        with self.assertRaises(RuntimeError):
            async with database.locked_pro_slot(pool, 5) as (slot, active_cursor):
                self.assertEqual(slot, ("reservation",))
                self.assertIs(active_cursor, cursor)
                self.assertIn("FOR UPDATE", cursor.execute.await_args.args[0])
                raise RuntimeError("Discord rejected the edit")
        conn.rollback.assert_awaited_once()
        conn.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
