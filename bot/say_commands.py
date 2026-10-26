"""Publication de messages du staff avec l'identité du bot."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


def setup(bot: commands.Bot):
    @bot.tree.command(name="kanasay", description="(Staff) Publie un message avec le bot dans un salon texte")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.cooldown(1, 5.0, key=lambda interaction: (interaction.guild_id, interaction.user.id))
    @app_commands.describe(
        salon="Salon texte où publier (tu dois pouvoir y gérer et envoyer des messages)",
        message="Texte à publier, de 1 à 2000 caractères ; les mentions ne notifient personne",
    )
    async def kanasay(
        interaction: discord.Interaction,
        salon: discord.TextChannel,
        message: app_commands.Range[str, 1, 2000],
    ):
        guild = interaction.guild
        # Les permissions par défaut des slash commands peuvent être changées dans
        # Discord : le contrôle effectif reste indispensable à chaque invocation.
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Cette commande s'utilise sur le serveur.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Il te faut la permission « Gérer les messages » sur le serveur.", ephemeral=True
            )
            return
        if not isinstance(salon, discord.TextChannel) or salon.guild.id != guild.id:
            await interaction.response.send_message("❌ Choisis un salon texte de ce serveur.", ephemeral=True)
            return
        if not message.strip() or len(message) > 2000:
            await interaction.response.send_message(
                "❌ Le message doit contenir entre 1 et 2000 caractères et ne peut pas être vide.", ephemeral=True
            )
            return

        user_permissions = salon.permissions_for(interaction.user)
        if not all((user_permissions.view_channel, user_permissions.send_messages, user_permissions.manage_messages)):
            await interaction.response.send_message(
                "❌ Tu dois pouvoir voir ce salon, y envoyer et y gérer les messages.", ephemeral=True
            )
            return
        bot_member = guild.me
        bot_permissions = salon.permissions_for(bot_member) if bot_member is not None else None
        if bot_permissions is None or not bot_permissions.view_channel or not bot_permissions.send_messages:
            await interaction.response.send_message(
                "❌ Il me faut les permissions « Voir le salon » et « Envoyer des messages » dans ce salon.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            sent = await salon.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord refuse l'envoi. Vérifie mes permissions dans le salon choisi.", ephemeral=True
            )
            return
        except discord.NotFound:
            await interaction.followup.send("❌ Ce salon a été supprimé ou n'est plus accessible.", ephemeral=True)
            return
        except discord.HTTPException:
            logger.exception("Échec /kanasay : membre=%s salon=%s", interaction.user.id, salon.id)
            await interaction.followup.send(
                "❌ Discord n'a pas confirmé l'envoi. Vérifie le salon avant de réessayer.", ephemeral=True
            )
            return

        logger.info(
            "/kanasay : serveur=%s membre=%s salon=%s message=%s",
            guild.id, interaction.user.id, salon.id, sent.id,
        )
        await interaction.followup.send(
            f"✅ [Message publié dans le salon choisi]({sent.jump_url}).", ephemeral=True
        )

    @kanasay.error
    async def kanasay_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            content = f"⏳ Attends encore {error.retry_after:.1f} secondes avant un nouvel envoi."
        else:
            logger.error("Erreur /kanasay", exc_info=(type(error), error, error.__traceback__))
            content = "❌ Une erreur est survenue. Vérifie le salon choisi avant de réessayer."
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
