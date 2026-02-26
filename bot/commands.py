import logging
import os
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
import asyncio
import unicodedata
import re
import random

from . import config, database, helpers, state
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger(__name__)

# -----------------------
# Utils / format helpers
# -----------------------
def format_pokeweed_display(name, power, hp, rarity, owned=0):
    stars = {
        "Commun": "🌿",
        "Peu Commun": "🌱🌿",
        "Rare": "🌟",
        "Très Rare": "💎",
        "Légendaire": "🌈👑",
    }
    flair = {
        "Commun": "",
        "Peu Commun": "*",
        "Rare": "**",
        "Très Rare": "***",
        "Légendaire": "__**"
    }
    flair_end = {
        "Commun": "",
        "Peu Commun": "*",
        "Rare": "**",
        "Très Rare": "***",
        "Légendaire": "**__"
    }

    status = "🆕 Nouvelle carte !" if owned == 0 else f"x{owned + 1}"
    return f"{stars.get(rarity, '🌿')} {flair[rarity]}{name}{flair_end[rarity]} — 💥 {power} | ❤️ {hp} | ✨ {rarity} ({status})"

# Set global pour bloquer le double-clic (anti-cheat)
_inflight_claims: set[int] = set()

class ClaimPokeweedView(discord.ui.View):
    def __init__(self, user_id: int, pokeweed_id: int, pokeweed_name: str, points_value: int, total_owned: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.pokeweed_id = pokeweed_id
        self.pokeweed_name = pokeweed_name
        self.points_value = points_value
        self.total_owned = total_owned

        # Choix de la couleur : rouge s'il n'en a qu'un (attention danger), vert sinon
        btn_style = discord.ButtonStyle.danger if total_owned == 1 else discord.ButtonStyle.success
        label = f"Vendre l'unique ({points_value} pts) 💰" if total_owned == 1 else f"Vendre 1 double ({points_value} pts) 💰"

        self.claim_btn = discord.ui.Button(label=label, style=btn_style, custom_id=f"claim_{pokeweed_id}")
        self.claim_btn.callback = self.claim_callback
        self.add_item(self.claim_btn)

    async def claim_callback(self, interaction: discord.Interaction):
        # Sécurité 1 : Vérifie si c'est bien l'auteur de la commande
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bas les pattes, ce n'est pas ton Pokédex !", ephemeral=True)
            return

        # Sécurité 2 : Empêche le spam de clics
        if self.user_id in _inflight_claims:
            await interaction.response.send_message("⏳ Transaction déjà en cours, doucement...", ephemeral=True)
            return

        _inflight_claims.add(self.user_id)
        try:
            # 🛠️ CORRECTION ICI : On defer la mise à jour du composant (sans recréer de message éphémère)
            await interaction.response.defer()

            # Sécurité 3 : Limite de 10 ventes par 5 heures
            sales_count = await database.get_recent_sales_count(database.db_pool, self.user_id, hours=5)
            if sales_count >= 10:
                await interaction.followup.send("❌ Tu as atteint la limite de **10 ventes par 5 heures**. Reviens plus tard frérot !", ephemeral=True)
                return

            # Exécution de la vente
            success = await database.sell_pokeweed(database.db_pool, self.user_id, self.pokeweed_id, self.points_value)

            if not success:
                self.claim_btn.disabled = True
                # 🛠️ CORRECTION ICI : On utilise edit_original_response au lieu de message.edit
                await interaction.edit_original_response(view=self)
                await interaction.followup.send(f"❌ Impossible de vendre {self.pokeweed_name}. (As-tu déjà tout vendu ?)", ephemeral=True)
                return

            self.total_owned -= 1
            sales_count += 1

            # Mise à jour des grades s'il a dépassé un palier grâce à l'argent
            from . import helpers # Assure-toi que helpers est bien importé ou accessible
            new_total = await database.get_user_points(database.db_pool, str(self.user_id))
            await helpers.update_member_prestige_role(interaction.user, new_total)

            # Modification dynamique du bouton
            if self.total_owned > 0:
                self.claim_btn.label = f"Vendre 1 double ({self.points_value} pts) 💰 [{10 - sales_count}/10]"
                if self.total_owned == 1:
                    self.claim_btn.style = discord.ButtonStyle.danger
                    self.claim_btn.label = f"Vendre l'unique ({self.points_value} pts) 💰 [{10 - sales_count}/10]"
            else:
                self.claim_btn.label = "Plus de cartes ❌"
                self.claim_btn.disabled = True

            # 🛠️ CORRECTION ICI AUSSI
            await interaction.edit_original_response(view=self)
            
            await interaction.followup.send(f"✅ Vente réussie ! **+{self.points_value} pts** pour {self.pokeweed_name}.", ephemeral=True)

        except Exception as e:
            logger.exception(f"Erreur claim_callback pour {self.user_id} : {e}")
            await interaction.followup.send("❌ Une erreur est survenue lors de la transaction.", ephemeral=True)
        finally:
            _inflight_claims.discard(self.user_id)

class LivePreviewView(discord.ui.View):
    def __init__(self, bot, author, content_to_send):
        super().__init__(timeout=120) # 2 minutes pour confirmer
        self.bot = bot
        self.author = author
        self.content_to_send = content_to_send

    @discord.ui.button(label="Confirmer l'annonce ✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ Touche pas à ça frérot, c'est pas ton annonce.", ephemeral=True)
            return
        
        # On revérifie la limite au cas où
        count = await database.get_weekly_live_count(database.db_pool, self.author.id)
        if count >= 3:
            await interaction.response.send_message("❌ T'as déjà atteint ta limite de 3 annonces sur les 7 derniers jours !", ephemeral=True)
            return

        # On enregistre l'annonce dans la BDD
        await database.add_live_announcement(database.db_pool, self.author.id)
        
        # On envoie dans le salon des annonces
        channel = self.bot.get_channel(config.CHANNEL_ANNONCES_ID)
        if channel:
            await channel.send(self.content_to_send)
            
        # On désactive les boutons
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="✅ **Ton live a été annoncé avec succès !** Bon stream frérot 🌿", view=self)

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return
            
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ **Annonce annulée.** T'as eu un coup de pression ?", view=self)

class TradeOfferView(discord.ui.View):
    def __init__(self, u1: discord.Member, u2: discord.Member, p1_id: int, p2_id: int, p1_name: str, p2_name: str):
        super().__init__(timeout=7200) # 2 heures
        self.u1 = u1
        self.u2 = u2
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name

    @discord.ui.button(label="Accepter l'échange ✅", style=discord.ButtonStyle.success)
    async def btn_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.u2.id:
            await interaction.response.send_message("❌ Bas les pattes, cet échange ne t'est pas adressé !", ephemeral=True)
            return

        # Sécurité anti-spam
        if self.u1.id in _inflight_claims or self.u2.id in _inflight_claims:
            await interaction.response.send_message("⏳ L'un de vous a déjà une transaction en cours, doucement...", ephemeral=True)
            return

        _inflight_claims.update([self.u1.id, self.u2.id])
        try:
            await interaction.response.defer()
            # L'exécution ultra sécurisée de l'échange
            success = await database.execute_trade(database.db_pool, self.u1.id, self.p1_id, self.u2.id, self.p2_id)
            
            for child in self.children:
                child.disabled = True
            
            if success:
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.title = "🤝 Échange terminé avec succès !"
                await interaction.edit_original_response(embed=embed, view=self)
                await interaction.followup.send(f"🎉 Échange réussi ! {self.u1.mention} récupère **{self.p2_name}** et {self.u2.mention} récupère **{self.p1_name}** !")
            else:
                await interaction.edit_original_response(content="❌ **Échange annulé.** Quelqu'un a vendu sa carte entre-temps ou un problème est survenu !", embed=None, view=self)
        finally:
            _inflight_claims.discard(self.u1.id)
            _inflight_claims.discard(self.u2.id)

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.danger)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.u1.id, self.u2.id]:
            await interaction.response.send_message("❌ Tu n'es pas dans cet échange.", ephemeral=True)
            return
            
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"🚫 Échange annulé par {interaction.user.mention}.", embed=None, view=self)


class TradePreviewView(discord.ui.View):
    def __init__(self, bot, u1: discord.Member, u2: discord.Member, p1_id: int, p2_id: int, p1_name: str, p2_name: str):
        super().__init__(timeout=120)
        self.bot = bot
        self.u1 = u1
        self.u2 = u2
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.p1_name = p1_name
        self.p2_name = p2_name

    @discord.ui.button(label="Confirmer et Proposer ✅", style=discord.ButtonStyle.success)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.u1.id:
            return
            
        # On remplace l'embed éphémère
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="✅ Ta demande d'échange a été envoyée sur le salon !", embed=None, view=self)
        
        # On envoie le VRAI message de proposition sur le channel
        channel = interaction.channel
        embed = discord.Embed(
            title="🔄 Proposition d'Échange Pokéweed",
            description=f"{self.u2.mention}, tu as **2 heures** pour répondre à l'offre de {self.u1.mention} !",
            color=discord.Color.gold()
        )
        embed.add_field(name=f"Ce que propose {self.u1.display_name} :", value=f"🌿 **{self.p1_name}**", inline=False)
        embed.add_field(name="Ce qu'il veut en retour :", value=f"🌿 **{self.p2_name}**", inline=False)
        
        view = TradeOfferView(self.u1, self.u2, self.p1_id, self.p2_id, self.p1_name, self.p2_name)
        await channel.send(content=self.u2.mention, embed=embed, view=view)

    @discord.ui.button(label="Annuler ❌", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.u1.id:
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ **Prévisualisation annulée.** L'échange n'a pas été proposé.", embed=None, view=self)


class LiveModal(discord.ui.Modal, title='Annonce ton Live Twitch !'):
    titre = discord.ui.TextInput(
        label='Titre de ton annonce',
        placeholder='Ex: SOIRÉE SMOKE CHILL, TRYHARD RANKED DEF...',
        style=discord.TextStyle.short,
        required=True,
        max_length=100
    )
    
    jeu = discord.ui.TextInput(
        label='Sur quel jeu ?',
        placeholder='Ex: Just Chatting, Valorant, GTA V...',
        style=discord.TextStyle.short,
        required=True,
        max_length=50
    )

    lien = discord.ui.TextInput(
        label='Lien de ta chaîne Twitch (entier)',
        placeholder='Ex: https://twitch.tv/kanae420',
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, count: int):
        super().__init__()
        self.count = count # On passe le compte actuel pour l'afficher

    async def on_submit(self, interaction: discord.Interaction):
        
        message_content = (
            f"🔴 **{self.titre.value.upper()}** 📣\n\n"
            f"{interaction.user.mention} lance un live sur **{self.jeu.value}**.\n"
            f"**Viens en fumer un long, t'es le/la bienvenu(e) 🚬!\n\n**"
            f"_(Aucun point kanaé ne sera distribué durant ce live)_\n\n"
            f"{self.lien.value}\n\n"
        )
        
        # Message de prévisualisation
        preview_text = (
            f"👀 **PRÉVISUALISATION DE TON ANNONCE**\n"
            f"*Il te reste {2 - self.count} annonce(s) possible(s) cette semaine après celle-ci.*\n"
            f"----------------------------------\n\n"
            f"{message_content}"
        )
        
        # On envoie la prévisu avec les boutons Confirmer/Annuler
        view = LivePreviewView(interaction.client, interaction.user, message_content)
        await interaction.response.send_message(preview_text, view=view, ephemeral=True)            

class DouilleView(discord.ui.View):
    def __init__(self, host_id: int, mise: int):
        super().__init__(timeout=60.0) # Les joueurs ont 60 secondes pour rejoindre
        self.host_id = host_id
        self.mise = mise
        self.players = {host_id} # Le créateur est automatiquement dedans
        
    @discord.ui.button(label="Rejoindre la partie 🔫", style=discord.ButtonStyle.danger, custom_id="join_douille")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        if user_id in self.players:
            await interaction.response.send_message("❌ T'es déjà dans la partie frérot, calme-toi !", ephemeral=True)
            return
            
        if len(self.players) >= 6:
            await interaction.response.send_message("❌ Le barillet est plein (6 joueurs max) !", ephemeral=True)
            return
            
        # On vérifie si le joueur a assez de points pour suivre la mise (Mois + À Vie)
        current_points = await database.get_user_points(database.db_pool, str(user_id))
        monthly_points = await database.get_user_monthly_points(database.db_pool, str(user_id))
        solde_jouable = min(current_points, monthly_points)

        if solde_jouable < self.mise:
            await interaction.response.send_message(
                f"❌ T'es à sec ! Il te faut au moins **{self.mise} points jouables** pour rejoindre.\n"
                f"*(Rappel: Tu as {monthly_points} pts ce mois-ci et {current_points} pts à vie)*", 
                ephemeral=True
            )
            return
            
        self.players.add(user_id)
        await interaction.response.send_message(f"✅ Tu as rejoint la partie pour {self.mise} points !", ephemeral=True)
        
        # On met à jour le message public avec les nouveaux joueurs
        mentions = " ".join([f"<@{pid}>" for pid in self.players])
        embed = interaction.message.embeds[0]
        embed.description = f"**Mise :** {self.mise} points\n**Joueurs ({len(self.players)}/6) :**\n{mentions}\n\n*Cliquez sur le bouton pour rejoindre ! Le barillet tourne dans quelques secondes.*"
        await interaction.message.edit(embed=embed)
        
        # Si on atteint 6 joueurs, on lance la partie direct sans attendre la fin du chrono
        if len(self.players) >= 6:
            self.stop()

class CandidatureModal(discord.ui.Modal, title='Candidature Staff Kanaé'):
    # On définit les champs que l'utilisateur devra remplir
    poste = discord.ui.TextInput(
        label='Quel poste vises-tu ?',
        placeholder='Ex: Modérateur, Animateur, Helper...',
        style=discord.TextStyle.short,
        required=True,
        max_length=50
    )
    
    age = discord.ui.TextInput(
        label='Ton âge',
        placeholder='Ex: 21',
        style=discord.TextStyle.short,
        required=True,
        max_length=2
    )

    dispos = discord.ui.TextInput(
        label='Tes disponibilités',
        placeholder='Ex: Tous les soirs après 18h et le week-end',
        style=discord.TextStyle.short,
        required=True,
        max_length=100
    )

    motivation = discord.ui.TextInput(
        label='Pourquoi toi ? (Motivations)',
        placeholder='Dis-nous pourquoi tu ferais un bon membre de l\'équipe...',
        style=discord.TextStyle.long, # Champ plus grand pour un texte long
        required=True,
        max_length=1000
    )

    # Ce qui se passe quand le mec clique sur "Envoyer"
    async def on_submit(self, interaction: discord.Interaction):
        # 1. On confirme à l'utilisateur que c'est bon
        await interaction.response.send_message(
            "✅ Ta candidature a bien été envoyée au staff. Merci pour ton implication frérot !", 
            ephemeral=True
        )

        # 2. On récupère le salon privé du staff
        channel = interaction.client.get_channel(config.CHANNEL_RECRUTEMENT_ID)
        
        if channel:
            # 3. On crée un bel Embed pour le staff
            embed = discord.Embed(
                title=f"📝 Nouvelle Candidature : {self.poste.value}",
                color=discord.Color.gold(),
                timestamp=interaction.created_at
            )
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="Âge", value=self.age.value, inline=True)
            embed.add_field(name="Disponibilités", value=self.dispos.value, inline=True)
            embed.add_field(name="Motivations", value=self.motivation.value, inline=False)
            embed.set_footer(text=f"ID User : {interaction.user.id}")

            await channel.send(embed=embed)
        else:
            logger.error("❌ Impossible de trouver le salon de recrutement. Vérifie CHANNEL_RECRUTEMENT_ID.")

def setup(bot: commands.Bot):
    # ---------------------------------------
    # /hey
    # ---------------------------------------
    @bot.tree.command(name="hey", description="Parle avec Kanaé, l'IA officielle du serveur !")
    @app_commands.describe(message="Ton message à envoyer")
    async def hey(interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {config.MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "agent_id": config.AGENT_ID_MISTRAL,
                    "messages": [{"role": "user", "content": message}],
                }
                async with session.post(
                    "https://api.mistral.ai/v1/agents/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response_text = data['choices'][0]['message']['content']
                    else:
                        response_text = f"Yo, Mistral a répondu {resp.status}. J'sais pas ce qu'il veut là frérot."
        except Exception as e:
            logger.error("Mistral API error: %s", e)
            response_text = "Yo, j'crois que Mistral est en PLS là, réessaye plus tard."
        await interaction.followup.send(response_text, ephemeral=True)

    # ---------------------------------------
    # /score
    # ---------------------------------------
    @bot.tree.command(name="score", description="Affiche ton score ou celui d’un autre membre")
    @app_commands.describe(membre="Le membre dont tu veux voir le score")
    async def score_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre if membre else interaction.user
        user_id = str(target.id)

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Récupérer classement global
                await cur.execute("SELECT user_id, points FROM scores ORDER BY points DESC;")
                global_rows = await cur.fetchall()
                # Récupérer classement mensuel
                await cur.execute("SELECT user_id, points FROM monthly_scores ORDER BY points DESC;")
                monthly_rows = await cur.fetchall()

        # Fonction locale pour calculer la position et les points en ignorant les exclus
        def get_rank_and_score(rows):
            filtered = []
            for uid, pts in rows:
                member_obj = interaction.guild.get_member(int(uid))
                if member_obj and any(role.id == config.EXCLUDED_ROLE_ID for role in member_obj.roles):
                    continue
                filtered.append((uid, pts))
            
            position = None
            user_score = 0
            for i, (uid, pts) in enumerate(filtered, 1):
                if str(uid) == user_id:
                    position = i
                    user_score = pts
                    break
            return position, user_score

        global_pos, global_score = get_rank_and_score(global_rows)
        monthly_pos, monthly_score = get_rank_and_score(monthly_rows)

        # Création de l'Embed stylé
        embed = discord.Embed(
            title=f"🏆 Profil de {target.display_name}",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        rang_mensuel = f"(Rang #{monthly_pos})" if monthly_pos else "(Aucun point ce mois-ci)"
        embed.add_field(
            name="🥇 Kanaé d'Or (Ce mois-ci)", 
            value=f"**{monthly_score} points** {rang_mensuel}", 
            inline=False
        )
        
        rang_global = f"(Rang #{global_pos})" if global_pos else "(Aucun point total)"
        embed.add_field(
            name="🌟 Score à vie (Total)", 
            value=f"**{global_score} points** {rang_global}", 
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------------------------------
    # /top (Mois et À vie)
    # ---------------------------------------
    @bot.tree.command(name="top", description="Affiche le classement des meilleurs fumeurs")
    @app_commands.describe(categorie="Choisis quel classement tu veux voir")
    @app_commands.choices(categorie=[
        app_commands.Choice(name="🏆 Mensuel (Kanaé d'Or)", value="mois"),
        app_commands.Choice(name="🌟 À vie (Panthéon)", value="vie"),
    ])
    async def top(interaction: discord.Interaction, categorie: app_commands.Choice[str]):
        is_monthly = (categorie.value == "mois")
        table = "monthly_scores" if is_monthly else "scores"
        header = "🏆 Classement du Mois : Kanaé d'Or 🏆" if is_monthly else "🌟 Classement à Vie : Panthéon 🌟"
        
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT user_id, points FROM {table} ORDER BY points DESC;")
                rows = await cur.fetchall()

        filtered = []
        for uid, pts in rows:
            member_obj = interaction.guild.get_member(int(uid))
            if member_obj and any(role.id == config.EXCLUDED_ROLE_ID for role in member_obj.roles):
                continue
            if pts > 0:
                filtered.append((uid, pts))
            if len(filtered) >= 5:
                break

        if not filtered:
            await interaction.response.send_message("📊 Pas encore de points enregistrés pour ce classement.", ephemeral=True)
            return

        icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        lines = [f"**{header}**\n"]
        for idx, (uid, pts) in enumerate(filtered):
            user = interaction.guild.get_member(int(uid))
            name = user.display_name if user else f"Utilisateur Inconnu"
            lines.append(f"{icons[idx]} {name} \u2192 **{pts} pts**")
        
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ---------------------------------------
    # /set (admin)
    # ---------------------------------------
    @bot.tree.command(name="set", description="Définit manuellement le score d'un utilisateur")
    @app_commands.describe(
        user_id="ID Discord de l'utilisateur", 
        nouveau_total="Nombre de points à définir",
        categorie="Quel score veux-tu modifier ?"
    )
    @app_commands.choices(categorie=[
        app_commands.Choice(name="🌟 Score à vie (Global)", value="vie"),
        app_commands.Choice(name="🏆 Score du Mois (Kanaé d'Or)", value="mois"),
    ])
    async def set_points(interaction: discord.Interaction, user_id: str, nouveau_total: int, categorie: app_commands.Choice[str]):
        # Petite sécurité admin au cas où
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        try:
            guild = interaction.guild
            member = guild.get_member(int(user_id))
            if not member:
                await interaction.response.send_message("❌ Utilisateur introuvable dans cette guild.", ephemeral=True)
                return
            
            # On envoie la valeur ("vie" ou "mois") à la fonction de base de données
            await database.set_user_points(database.db_pool, user_id, nouveau_total, categorie.value)
            
            # Message de confirmation stylé
            nom_categorie = "À VIE 🌟" if categorie.value == "vie" else "MENSUEL 🏆"
            await interaction.response.send_message(
                f"✅ Le score **{nom_categorie}** de {member.display_name} a été défini sur **{nouveau_total} points**.", 
                ephemeral=True
            )
        except Exception as e:
            logger.error("/set failed: %s", e)
            await interaction.response.send_message("❌ Une erreur est survenue en définissant le score.", ephemeral=True)

    # ---------------------------------------
    # /booster (SAFE)
    # ---------------------------------------
    _inflight_boosters: set[int] = set()

    def sanitize_filename(name: str) -> str:
        name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
        name = re.sub(r'[^a-zA-Z0-9]', '', name)
        return name.lower()

    @bot.tree.command(name="booster", description="Ouvre un booster de 4 Pokéweeds aléatoires !")
    async def booster(interaction: discord.Interaction):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        # Anti spam/double clic
        if user_id in _inflight_boosters:
            await interaction.response.send_message("⏳ Attends un peu frérot, booster déjà en cours...", ephemeral=True)
            return

        _inflight_boosters.add(user_id)
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Cooldown check
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT last_opened FROM booster_cooldowns WHERE user_id=%s;", (user_id,))
                    row = await cur.fetchone()
                    if row and row[0]:
                        last_time = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                        if (now - last_time) < timedelta(hours=12):
                            remaining = timedelta(hours=12) - (now - last_time)
                            h, m = remaining.seconds // 3600, (remaining.seconds % 3600) // 60
                            await interaction.edit_original_response(content=f"🕒 Attends encore **{h}h {m}min** pour un nouveau booster.")
                            return

            # Tirage des 4 cartes
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT * FROM pokeweeds ORDER BY RAND() LIMIT 4;")
                    rewards = await cur.fetchall()

            points_by_rarity = {"Commun": 2, "Peu Commun": 4, "Rare": 8, "Très Rare": 12, "Légendaire": 15}
            bonus_new = 5
            
            embeds = []
            files = []
            views = [] # ✅ Nouvelle liste pour stocker les boutons de vente
            inserts = []
            total_points = 0

            # Vérification des doublons et préparation des messages
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for pokeweed in rewards:
                        pid, name, hp, cap_pts, power, rarity = pokeweed[:6]
                        await cur.execute("SELECT COUNT(*) FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s;", (user_id, pid))
                        owned = (await cur.fetchone())[0]

                        # Points bonus
                        pts = points_by_rarity.get(rarity, 0)
                        if owned == 0:
                            pts += bonus_new
                        total_points += pts

                        # Préparation de l'Embed et de l'image
                        rarity_folder = rarity.lower().replace(" ", "").replace("é", "e")
                        filename = sanitize_filename(name) + ".png"
                        image_path = f"./assets/pokeweed/saison-1/{rarity_folder}/{filename}"
                        embed = discord.Embed(
                            title=f"{name} 🌿",
                            description=f"💥 Attaque : {power}\n❤️ Vie : {hp}\n✨ Rareté : {rarity}\n📦 {'🆕 Nouvelle carte !' if owned == 0 else f'x{owned + 1}'}",
                            color=discord.Color.green()
                        )

                        try:
                            file = discord.File(image_path, filename=filename)
                            embed.set_image(url=f"attachment://{filename}")
                            files.append(file)
                        except Exception:
                            embed.description += "\n⚠️ Image non trouvée."
                            files.append(None) # On garde l'index aligné pour la suite

                        embeds.append(embed)
                        inserts.append((user_id, pid))
                        
                        # ✅ Création du bouton Vendre pour CHAQUE carte tirée
                        total_owned = owned + 1 # Car il vient de l'obtenir
                        view = ClaimPokeweedView(user_id, pid, name, cap_pts, total_owned)
                        views.append(view)

            # ✅ MAJ DB en PREMIER : On sauvegarde les cartes et on reset le cooldown
            # (Obligatoire pour que le bouton Vendre fonctionne instantanément)
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for uid, pid in inserts:
                        await cur.execute("INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());", (uid, pid))
                    await database.add_points(database.db_pool, user_id, total_points)
                    final_pts = await database.get_user_points(database.db_pool, user_id)
                    await helpers.update_member_prestige_role(interaction.user, final_pts)
                    await cur.execute("INSERT INTO booster_cooldowns (user_id, last_opened) VALUES (%s, %s) ON DUPLICATE KEY UPDATE last_opened = %s;", (user_id, now, now))

            # ✅ Envoi de l'annonce Publique
            pokeweed_channel = interaction.client.get_channel(config.CHANNEL_POKEWEED_ID)
            stars = {
                "Commun": "🌿",
                "Peu Commun": "🌱🌿",
                "Rare": "🌟",
                "Très Rare": "💎",
                "Légendaire": "🌈👑",
            }
            resume_lines = [
                "🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀",
                "",
                f"🎉 {interaction.user.mention} a ouvert un **booster** et a obtenu :",
                ""
            ]
            for pokeweed in rewards:
                pid, name, hp, cap_pts, power, rarity = pokeweed[:6]
                resume_lines.append(f"{stars.get(rarity, '🌿')} {name} — 💥 {power} | ❤️ {hp} | ✨ {rarity}")

            resume_lines.append("")
            resume_lines.append("🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀")

            if pokeweed_channel:
                await pokeweed_channel.send("\n".join(resume_lines))

            # ✅ Envoi Privé au joueur (avec les Embeds, Images et Boutons)
            await interaction.edit_original_response(content=f"🃏 Booster ouvert ! 🎉 Tu gagnes **{total_points} points** dans le concours Kanaé !")
            
            for embed, file, view in zip(embeds, files, views):
                if file:
                    await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
                else:
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                await asyncio.sleep(0.3)

        except Exception as e:
            logger.exception(f"Erreur dans /booster pour {user_id} : {e}")
            await interaction.followup.send("❌ Une erreur est survenue. Réessaie un peu plus tard.", ephemeral=True)
        finally:
            _inflight_boosters.discard(user_id)


    # ---------------------------------------
    # /capture
    # ---------------------------------------
    @bot.tree.command(name="capture", description="Tente de capturer le Pokéweed sauvage")
    async def capture(interaction: discord.Interaction):
        if not state.current_spawn:
            await interaction.response.send_message("❌ Aucun Pokéweed à capturer maintenant...", ephemeral=True)
            return

        winner_id = getattr(state, "capture_winner", None)
        if winner_id:
            await interaction.response.send_message("❌ Trop tard, il a déjà été capturé !", ephemeral=True)
            return

        pokeweed = state.current_spawn
        user_id = interaction.user.id
        
        pid = pokeweed[0]
        name = pokeweed[1]
        cap_pts = pokeweed[3]

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. On vérifie s'il possède déjà la carte AVANT de lui donner
                await cur.execute("SELECT COUNT(*) FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s;", (user_id, pid))
                owned_before = (await cur.fetchone())[0]

                # 2. On insère la nouvelle capture
                await cur.execute(
                    "INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());",
                    (user_id, pid)
                )
                
                # 3. Ajout des points
                await database.add_points(database.db_pool, user_id, cap_pts)
                new_total = await database.get_user_points(database.db_pool, user_id)
                await helpers.update_member_prestige_role(interaction.user, new_total)

        # On verrouille la capture pour les autres joueurs
        state.capture_winner = user_id
        
        # Message public dans le salon
        channel = interaction.channel
        await channel.send(f"🎉 Bravo {interaction.user.mention} pour avoir capturé **{name}** ! +{cap_pts} points 🌿")
        
        # Message éphémère (Secret)
        if owned_before > 0:
            total_owned = owned_before + 1
            # On réutilise le bouton magique créé pour le Pokédex !
            view = ClaimPokeweedView(user_id, pid, name, cap_pts, total_owned)
            
            await interaction.response.send_message(
                f"🤫 **Pssst...** Tu as bien capturé **{name}**, mais tu l'avais déjà (tu en as {total_owned} maintenant) !\n\n"
                f"Tu voudrais pas le vendre tout de suite ?", 
                view=view, 
                ephemeral=True
            )
        else:
            # S'il ne l'avait pas, on le félicite juste
            await interaction.response.send_message("✅ Tu l’as capturé ! 🆕 C'est une toute nouvelle carte pour ton Pokédex !", ephemeral=True)

    # ---------------------------------------
    # /pokedex
    # ---------------------------------------
    # ✅ VERSION ILLUSTRÉE DU /pokedex
    # À intégrer dans commands.py — affiche chaque Pokéweed possédé avec image (embed par carte)

    # ✅ VERSION INTERACTIVE DU /pokedex AVEC BOUTONS PAR RARETÉ

    RARITY_ORDER = [
        ("Commun", "🌿"),
        ("Peu Commun", "🌱🌿"),
        ("Rare", "🌟"),
        ("Très Rare", "💎"),
        ("Légendaire", "🌈👑")
    ]

    def sanitize_filename(name: str) -> str:
        import unicodedata, re
        name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
        return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

    class RarityButton(discord.ui.Button):
        def __init__(self, rarity, emoji, user, pokes):
            label = f"{emoji} {rarity}"
            super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=rarity)
            self.rarity = rarity
            self.user = user
            self.pokes = pokes

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.user.id != self.user.id:
                await interaction.followup.send("❌ Ce Pokédex n’est pas le tien.", ephemeral=True)
                return

            if not self.pokes:
                await interaction.followup.send(f"📭 Tu n’as aucun Pokéweed de rareté **{self.rarity}**.", ephemeral=True)
                return

            for pid, name, hp, cap_pts, power, rarity_val, total, last_date in self.pokes:
                filename = sanitize_filename(name) + ".png"
                path = f"./assets/pokeweed/saison-1/{rarity_val.lower().replace(' ', '').replace('é', 'e')}/{filename}"
                date_str = last_date.strftime("%d %b %Y") if last_date else "?"

                embed = discord.Embed(
                    title=f"{name} 🌿",
                    description=f"💥 Attaque : {power}\n❤️ Vie : {hp}\n✨ Valeur : {cap_pts} pts\n📦 Possédé : x{total}\n📅 Dernière capture : {date_str}\n⭐ Rareté : {rarity_val}",
                    color=discord.Color.green()
                )
                
                # Ajout de la vue avec le bouton "Vendre"
                # (On empêche la vente si c'est le Pokedex d'un autre membre)
                view = ClaimPokeweedView(self.user.id, pid, name, cap_pts, total) if interaction.user.id == self.user.id else None

                if os.path.exists(path):
                    file = discord.File(path, filename=filename)
                    embed.set_image(url=f"attachment://{filename}")
                    await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)
                else:
                    embed.description += "\n⚠️ Image non trouvée."
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

                await asyncio.sleep(0.2)

            # ✅ Une fois TOUS les pokéweeds envoyés, on redonne les boutons
            await interaction.followup.send(
                content="👀 Tu veux regarder une autre rareté ? Clique sur un autre bouton ci-dessous.",
                view=RarityView(self.view.pokemons_by_rarity, self.view.user),
                ephemeral=True
            )

    class RarityView(discord.ui.View):
        def __init__(self, pokemons_by_rarity: dict, user: discord.User):
            super().__init__(timeout=300)
            self.pokemons_by_rarity = pokemons_by_rarity  # ✅ Ajouté
            self.user = user  # ✅ Ajouté

            for rarity, emoji in RARITY_ORDER:
                pokes = pokemons_by_rarity.get(rarity, [])
                self.add_item(RarityButton(rarity, emoji, user, pokes))

    @bot.tree.command(name="pokedex", description="Affiche ton Pokédex personnel ou celui d’un autre")
    @app_commands.describe(membre="Le membre dont tu veux voir le Pokédex")
    async def pokedex(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre if membre else interaction.user

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT p.id, p.name, p.hp, p.capture_points, p.power, p.rarity,
                        COUNT(*) as total, MAX(up.capture_date) as last_capture
                    FROM user_pokeweeds up
                    JOIN pokeweeds p ON up.pokeweed_id = p.id
                    WHERE up.user_id=%s
                    GROUP BY p.id;
                """, (target.id,))
                rows = await cur.fetchall()

                await cur.execute("SELECT COUNT(*) FROM pokeweeds;")
                total_available = (await cur.fetchone())[0]

        if not rows:
            await interaction.response.send_message(f"📘 {target.display_name} n’a capturé aucun Pokéweed...", ephemeral=True)
            return

        pokemons_by_rarity = {}
        for row in rows:
            pokemons_by_rarity.setdefault(row[5], []).append(row) # L'index passe de 4 à 5 pour la rareté

        unique_count = len(rows)
        total_count = sum(r[6] for r in rows) # L'index du total passe de 5 à 6
        missing = total_available - unique_count

        summary = (
            f"📘 **Pokédex de {target.display_name}**\n\n"
            f"✅ Cartes uniques : {unique_count}/{total_available}\n"
            f"📦 Total : {total_count} cartes\n"
            f"❗ Il manque encore **{missing}** Pokéweeds pour compléter le Pokédex !\n\n"
            "👆 Clique sur les boutons ci-dessous pour afficher les Pokéweeds par rareté.\n\n"
            "\u200b"
        )

        await interaction.response.send_message(
            summary,
            view=RarityView(pokemons_by_rarity, target),
            ephemeral=True
        )


    # ---------------------------------------
    # /init-pokeweeds (admin)
    # ---------------------------------------
    @bot.tree.command(name="init-pokeweeds", description="Insère les 31 Pokéweed de base")
    async def init_pokeweeds(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        strains = [
            ("Gelachu", 100, 10, 40, "Rare", 0.05),
            ("Bulba Kush", 90, 7, 30, "Commun", 0.20),
            ("Sourmander", 110, 9, 35, "Peu Commun", 0.15),
            ("Gluezor", 120, 12, 45, "Rare", 0.05),
            ("OGtortank", 105, 8, 32, "Peu Commun", 0.10),
            ("Widowlee", 95, 6, 28, "Commun", 0.25),
            ("Purplax", 80, 5, 22, "Commun", 0.30),
            ("Skyweedon", 115, 10, 38, "Rare", 0.07),
            ("Pineachu", 85, 7, 25, "Peu Commun", 0.12),
            ("AK-Dracau", 100, 8, 33, "Peu Commun", 0.12),
            ("Zkittlechu", 90, 6, 27, "Commun", 0.20),
            ("Jackasaur", 100, 8, 30, "Peu Commun", 0.10),
            ("Durbanape", 110, 9, 36, "Rare", 0.07),
            ("Lemonix", 95, 6, 26, "Commun", 0.22),
            ("Amnesir", 105, 9, 31, "Peu Commun", 0.10),
            ("Noctulight", 100, 7, 29, "Commun", 0.20),
            ("Weddinja", 110, 11, 37, "Rare", 0.05),
            ("Trainquaza", 100, 9, 34, "Peu Commun", 0.08),
            ("Piekachu", 90, 7, 28, "Commun", 0.22),
            ("Critidos", 105, 8, 32, "Peu Commun", 0.09),
            ("Crackchomp", 95, 6, 27, "Commun", 0.25),
            ("Dosidoof", 100, 8, 31, "Peu Commun", 0.10),
            ("Mimosaur", 90, 6, 26, "Commun", 0.22),
            ("Tangrowth OG", 85, 5, 24, "Commun", 0.30),
            ("Forbiddenite", 115, 12, 40, "Rare", 0.04),
            ("Slurrizard", 100, 8, 33, "Peu Commun", 0.12),
            ("Runflare", 110, 10, 36, "Rare", 0.06),
            ("Gmokémon", 120, 13, 42, "Très Rare", 0.03),
            ("Maclax", 110, 9, 35, "Rare", 0.05),
            ("Sherbizard", 95, 7, 29, "Commun", 0.22),
            ("Kanéclor", 150, 20, 60, "Légendaire", 0.01)
        ]

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for s in strains:
                    await cur.execute("INSERT INTO pokeweeds (name, hp, capture_points, power, rarity, drop_rate) VALUES (%s,%s,%s,%s,%s,%s);", s)

        await interaction.response.send_message("🌿 31 Pokéweed insérés !", ephemeral=True)

    # ---------------------------------------
    # /link-twitch
    # ---------------------------------------
    @bot.tree.command(name="link-twitch", description="Lie ton compte Twitch")
    @app_commands.describe(pseudo_twitch="Ton pseudo Twitch")
    async def link_twitch(interaction: discord.Interaction, pseudo_twitch: str):

        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        username = pseudo_twitch.strip().lower()
        existing_twitch = await database.get_social_by_discord(
            database.db_pool, 
            discord_id, 
            "twitch"
        )

        if existing_twitch:
            await interaction.followup.send(
                f"❌ Tu as déjà lié le compte Twitch **{existing_twitch}** à ton profil.\n"
                "Si tu veux changer de compte, utilise d'abord la commande `/unlink-twitch` frérot !", 
                ephemeral=True
            )
            return

        # --- Validation format ---
        if not re.fullmatch(r"[a-z0-9_]{4,25}", username):
            await interaction.followup.send("❌ Pseudo Twitch invalide.", ephemeral=True)
            return

        if username == config.TWITCH_CHANNEL.lower():
            await interaction.followup.send("❌ Impossible de lier la chaîne officielle.", ephemeral=True)
            return

        # --- Récupération du token dynamique ---
        token = config.TWITCH_API_TOKEN
        if not token:
            await interaction.followup.send("❌ Erreur de connexion à Twitch.", ephemeral=True)
            return

        headers = {
            "Client-ID": config.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}" # 👈 On utilise le token frais
        }
        # --- Vérifie que le compte existe ---
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.twitch.tv/helix/users",
                headers=headers,
                params={"login": username}
            ) as resp:
                data = await resp.json()
                
                # NOUVEAU : On check si Twitch nous engueule
                if resp.status != 200:
                    logger.error(f"Erreur API Twitch [{resp.status}] : {data}")
                    await interaction.followup.send(f"❌ Twitch a bloqué la requête (Erreur {resp.status}). Regarde la console du bot pour les détails !", ephemeral=True)
                    return

        if not data.get("data"):
            await interaction.followup.send("❌ Compte Twitch introuvable.", ephemeral=True)
            return

        twitch_user_id = data["data"][0]["id"]

        # --- Empêche multi-link ---
        success = await database.link_social_account(
            database.db_pool,
            discord_id,
            "twitch",
            username
        )

        if not success:
            await interaction.followup.send("❌ Ce compte Twitch est déjà utilisé.", ephemeral=True)
            return

        # --- Vérif follow immédiate ---
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.twitch.tv/helix/channels/followers",
                headers=headers,
                params={
                    "broadcaster_id": config.TWITCH_BROADCASTER_ID,
                    "user_id": twitch_user_id
                }
            ) as resp:
                follow_data = await resp.json()

        logger.info(f"Vérification follow Twitch pour {username} ({twitch_user_id}) : {follow_data}")
        is_following = len(follow_data.get("data", [])) > 0

        if is_following:
            if await database.check_and_reward_social_link(database.db_pool, discord_id, "twitch", username):
                await database.add_points(database.db_pool, discord_id, 200)
                await interaction.followup.send("✅ Compte lié + Follow détecté 🎁 +200 points !", ephemeral=True)
            else:
                await interaction.followup.send("✅ Compte lié (Follow déjà validé).", ephemeral=True)
        else:
            await interaction.followup.send("✅ Compte lié. Follow non détecté pour le moment.", ephemeral=True)

    
    # ---------------------------------------
    # /unlink-twitch
    # ---------------------------------------
    @bot.tree.command(name="unlink-twitch", description="Délie ton compte Twitch de ton profil Discord")
    async def unlink_twitch(interaction: discord.Interaction):
        user_id = interaction.user.id
        platform = "twitch"
        
        existing_twitch = await database.get_social_by_discord(database.db_pool, user_id, platform)
        
        if not existing_twitch:
            await interaction.response.send_message(
                "❌ T'as aucun compte Twitch lié pour le moment frérot. Tu peux utiliser `/link-twitch` pour en ajouter un !", 
                ephemeral=True
            )
            return
            
        try:
            await database.unlink_social_account(database.db_pool, user_id, platform)
            await interaction.response.send_message(
                f"🗑️ C'est fait ! Ton ancien pseudo Twitch (**{existing_twitch}**) a été délié de ton compte.\n"
                f"Tu peux maintenant en lier un nouveau si tu veux.", 
                ephemeral=True
            )
        except Exception as e:
            logger.error("Erreur unlink-twitch: %s", e)
            await interaction.response.send_message(
                "❌ Une erreur est survenue lors de la suppression. Réessaie plus tard.", 
                ephemeral=True
            )
    # ---------------------------------------
    # /refresh-points
    # ---------------------------------------
    @bot.tree.command(name="refresh-points", description="Vérifie tes réseaux Twitch")
    async def refresh_points(interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)

        twitch_username = await database.get_social_by_discord(
            database.db_pool,
            discord_id,
            "twitch"
        )

        if not twitch_username:
            await interaction.followup.send("❌ Aucun compte Twitch lié.", ephemeral=True)
            return

        token = config.TWITCH_API_TOKEN
        if not token:
            await interaction.followup.send("❌ Impossible de contacter Twitch.", ephemeral=True)
            return

        headers = {
            "Client-ID": config.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        }

        # --- Récupère user_id Twitch ---
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.twitch.tv/helix/users",
                headers=headers,
                params={"login": twitch_username}
            ) as resp:
                user_data = await resp.json()

        if not user_data.get("data"):
            await interaction.followup.send("❌ Compte Twitch invalide.", ephemeral=True)
            return

        twitch_user_id = user_data["data"][0]["id"]

        total_gained = 0
        report = ["🔎 Vérification Twitch", ""]

        async with aiohttp.ClientSession() as session:

            # ---------- FOLLOW ----------
            async with session.get(
                "https://api.twitch.tv/helix/channels/followers",
                headers=headers,
                params={
                    "broadcaster_id": config.TWITCH_BROADCASTER_ID,
                    "user_id": twitch_user_id
                }
            ) as resp:
                follow_data = await resp.json()

            is_following = len(follow_data.get("data", [])) > 0

            if is_following:
                if await database.check_and_reward_social_link(database.db_pool, discord_id, "twitch", twitch_username):
                    total_gained += 200
                    report.append("✅ Follow validé : +200 pts")
                else:
                    report.append("✅ Follow déjà validé")
            else:
                report.append("❌ Follow non détecté")

            # ---------- SUB ----------
            async with session.get(
                "https://api.twitch.tv/helix/subscriptions",
                headers=headers,
                params={
                    "broadcaster_id": config.TWITCH_BROADCASTER_ID,
                    "user_id": twitch_user_id
                }
            ) as resp:
                if resp.status == 200:
                    sub_data = await resp.json()
                    is_sub = len(sub_data.get("data", [])) > 0
                else:
                    is_sub = False

            if is_sub:
                if await database.claim_twitch_sub_reward(database.db_pool, discord_id):
                    total_gained += 1000
                    report.append("💎 Sub validé : +1000 pts")
                else:
                    report.append("💎 Sub déjà récupéré ce mois-ci")
            else:
                report.append("❌ Sub non détecté")

        if total_gained > 0:
            new_total = await database.add_points(database.db_pool, discord_id, total_gained)
            await database.add_points(database.db_pool, discord_id, total_gained)
            report.append(f"\n🎁 TOTAL : +{total_gained} points")
            await helpers.update_member_prestige_role(interaction.user, new_total)

        await interaction.followup.send("\n".join(report), ephemeral=True)
    
    # ---------------------------------------
    # /live
    # ---------------------------------------
    @bot.tree.command(name="live", description="Ouvre le formulaire pour annoncer ton stream Twitch à la commu' !")
    async def live_cmd(interaction: discord.Interaction):
        user_id = interaction.user.id
        
        # 1. Vérification de la limite AVANT d'ouvrir la modale
        count = await database.get_weekly_live_count(database.db_pool, user_id)
        if count >= 3:
            await interaction.response.send_message(
                "❌ **Limite atteinte !** Tu as déjà fait 3 annonces dans les 7 derniers jours. Laisse de la place aux autres frérot 🌿.", 
                ephemeral=True
            )
            return
            
        # 2. Si c'est bon, on envoie la modale à l'écran du joueur
        await interaction.response.send_modal(LiveModal(count))

    # ---------------------------------------
    # /help-concours
    # ---------------------------------------
    @bot.tree.command(name="help-concours", description="Affiche toutes les façons de gagner des points pour le Kanaé d'Or !")
    async def help_concours(interaction: discord.Interaction):
        message = (
            "🏆 **GUIDE DU CONCOURS KANAÉ D'OR** 🏆\n\n"
            "💸 **Soutien & Croissance (Le Jackpot)**\n"
            "   • 💎 **Boost Discord :** +1000 points instantanés pour le soutien !\n"
            "   • 💜 **Twitch Sub :** +1000 points / mois (via `/refresh-points`)\n"
            "   • 🔗 **Twitch Follow :** +200 points (1 seule fois, via `/refresh-points`)\n"
            "   • 🤝 **Parrainage :** +250 points si ton invité reste au moins 2 heures\n\n"
            "🎰 **Économie & Casino**\n"
            "   • 🌅 **`/wakeandbake` :** +50 points par jour (jusqu'à 100 pts si tu as une bonne série) !\n"
            "   • 🎲 **`/bet` & `/douille` :** Multiplie tes points en jouant... ou perds tout !\n\n"
            "🗣️ **Activité Discord (Grind Quotidien)**\n"
            "   • 🎙️ **Vocal :** +15 points toutes les 30 minutes passées en salon vocal\n"
            "   • 📸 **Médias :** +15 points par photo/vidéo postée (1 fois par jour et par salon spécial)\n"
            "   • ✨ **Réactions :** +2 points par émoji reçu sur tes messages\n\n"
            "🧵 **Le Forum (Threads)**\n"
            "   • 📝 **Créer un sujet :** +25 points (1 fois/jour)\n"
            "   • 💬 **Participer :** +5 points pour ta première réponse sur un sujet\n"
            "   • 👑 **Bonus Créateur :** +2 points quand quelqu'un te répond\n\n"
            "📺 **Activité Twitch**\n"
            "   • 💬 **Chat en live :** +1 point par message envoyé quand le live est ON (1 pt/minute max)\n\n"
            "🌿 **Mini-Jeu Pokéweed**\n"
            "   • 🃏 **`/booster` :** +2 à +15 points par carte (+5 pts si c'est une nouvelle !)\n"
            "   • ⚡ **`/capture` :** Gagne des points bonus si tu es le premier à attraper le sauvage\n\n"
            "🔥 *Que le meilleur gagne frérot !*"
        )
        await interaction.response.send_message(message, ephemeral=True)

    # ---------------------------------------
    # /help-commandes
    # ---------------------------------------
    @bot.tree.command(name="help-commandes", description="Liste et détaille toutes les commandes du KanaéBot !")
    async def help_commandes(interaction: discord.Interaction):
        message = (
            "🛠️ **GUIDE DES COMMANDES KANAÉBOT** 🛠️\n\n"
            "💬 **Général & IA**\n"
            "   • `/hey [message]` : Discute avec l'IA officielle du serveur.\n"
            "   • `/candidature` : Remplis le formulaire pour postuler dans le staff.\n\n"
            "🏆 **Économie & Jeux**\n"
            "   • `/score [@membre]` : Affiche ton score total ou celui d'un pote.\n"
            "   • `/top-5` : Affiche le classement des 5 plus gros fumeurs.\n"
            "   • `/wakeandbake` : 🌅 Ta récompense quotidienne gratuite (multiplicateur x2 max) !\n"
            "   • `/bet [mise]` : 🎰 Parie tes points (48% de chance de doubler).\n"
            "   • `/douille [mise]` : 🔫 Roulette russe multijoueur (jusqu'à 6 joueurs).\n\n"
            "🌿 **Mini-Jeu Pokéweed**\n"
            "   • `/booster` : Ouvre un paquet de 4 cartes Pokéweed (1 fois toutes les 12h).\n"
            "   • `/capture` : Dégaine le plus vite pour attraper le Pokéweed sauvage.\n"
            "   • `/pokedex [@membre]` : Affiche ta collection de cartes triées par rareté.\n\n"
            "📺 **Twitch & Réseaux**\n"
            "   • `/link-twitch [pseudo]` : Relie ton compte Twitch pour gagner tes points.\n"
            "   • `/unlink-twitch` : Délie ton compte si tu t'es trompé.\n"
            "   • `/mes-reseaux` : Affiche tes comptes sociaux reliés à Kanaé.\n"
            "   • `/refresh-points` : Récupère tes points liés à Twitch (Follow & Subs) !\n\n"
            "*(Seules tes commandes s'affichent, les commandes admin sont secrètes 🥷)*"
        )
        await interaction.response.send_message(message, ephemeral=True)
    
    # ---------------------------------------
    # /mes-reseaux
    # ---------------------------------------
    @bot.tree.command(name="mes-reseaux", description="Affiche la liste de tous tes réseaux sociaux liés à Kanaé")
    async def mes_reseaux(interaction: discord.Interaction):
        user_id = interaction.user.id
        
        # On récupère toute la liste de ses réseaux dans la base de données
        socials = await database.get_all_socials_by_discord(database.db_pool, user_id)
        
        if not socials:
            await interaction.response.send_message(
                "❌ Tu n'as lié aucun réseau pour le moment frérot. Utilise `/link-twitch` pour commencer !",
                ephemeral=True
            )
            return
            
        lines = ["🔗 **TES RÉSEAUX CONNECTÉS** 🔗", ""]
        
        # Un petit dictionnaire pour mettre des beaux emojis selon la plateforme
        platform_emojis = {
            "twitch": "🟪 Twitch",
            "youtube": "🟥 YouTube",
            "instagram": "📸 Instagram",
            "tiktok": "🎵 TikTok",
            "kick": "🟩 Kick"
        }
        
        for platform, username in socials:
            # Si on a un emoji prévu, on le met, sinon on met juste le nom avec une majuscule
            display_name = platform_emojis.get(platform.lower(), f"🌐 {platform.capitalize()}")
            lines.append(f"• {display_name} : **{username}**")
            
        lines.append("")
        lines.append("*(N'oublie pas de faire `/refresh-points` pour récupérer tes récompenses !)*")
        
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ---------------------------------------
    # /bet (Casino)
    # ---------------------------------------
    @bot.tree.command(name="bet", description="Parie tes points Kanaé ! 🎰")
    @app_commands.describe(mise="Le nombre de points que tu veux parier")
    async def bet(interaction: discord.Interaction, mise: int):
        # On ne met pas ephemeral=True pour que TOUT LE MONDE voie le résultat et rigole !
        await interaction.response.defer(ephemeral=False)

        user_id = str(interaction.user.id)

        # 1. Sécurité : Vérifier le montant
        if mise <= 0:
            await interaction.followup.send("❌ Frérot, tu dois parier un montant positif (au moins 1 point).", ephemeral=True)
            return

        # 2. Sécurité : Vérifier si l'utilisateur a assez de points (mois + vie)
        current_points = await database.get_user_points(database.db_pool, user_id)
        monthly_points = await database.get_user_monthly_points(database.db_pool, user_id)
        
        # Le solde jouable est le minimum entre sa richesse à vie et sa richesse du mois
        solde_jouable = min(current_points, monthly_points)

        if solde_jouable < mise:
            await interaction.followup.send(
                f"❌ T'es à sec ! Tu ne peux parier que ce que tu possèdes sur les DEUX compteurs (Maximum jouable: **{solde_jouable}**).\n"
                f"*(Rappel: tu as **{monthly_points} pts** ce mois-ci et **{current_points} pts** à vie)*.", 
                ephemeral=True
            )
            return

        # 3. Le fameux tirage au sort (1 à 100)
        roll = random.randint(1, 100)

        if roll <= 48:
            # 🎉 GAGNÉ (48% de chance : 1 à 48)
            new_total = await database.add_points(database.db_pool, user_id, mise)
            await helpers.update_member_prestige_role(interaction.user, new_total)
            
            embed = discord.Embed(
                title="🎰 CASINO KANAÉ - BINGO ! 🎉",
                description=f"Incroyable {interaction.user.mention} ! T'as eu le nez fin.\n\n"
                            f"✅ Tu as parié **{mise} points** et tu as **DOUBLÉ** ta mise !\n"
                            f"💰 Ton nouveau solde à vie : **{new_total} points**.",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            # 💸 PERDU (52% de chance : 49 à 100)
            # On soustrait la mise en envoyant un nombre négatif
            new_total = await database.add_points(database.db_pool, user_id, -mise)
            
            # Mise à jour du rôle (s'il perd beaucoup, il peut être rétrogradé)
            await helpers.update_member_prestige_role(interaction.user, new_total)
            
            embed = discord.Embed(
                title="🎰 CASINO KANAÉ - COUP DUR... 💸",
                description=f"Aïe coup dur pour {interaction.user.mention}...\n\n"
                            f"❌ Le KanaéBot a raflé la mise ! Tu viens de perdre **{mise} points**.\n"
                            f"📉 Ton nouveau solde à vie : **{new_total} points**.\n\n"
                            f"*La maison gagne toujours :) (Mais tu peux toujours recommencer !)*",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)

    # ---------------------------------------
    # /wakeandbake (Daily Reward)
    # ---------------------------------------
    @bot.tree.command(name="wakeandbake", description="Récupère ta récompense quotidienne. Fais grimper ton multiplicateur jusqu'à x2 ! 🌅")
    async def wakeandbake(interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # On vérifie directement en base AVANT de répondre à l'interaction
        success, streak, reward, multiplicateur = await database.claim_wake_and_bake(database.db_pool, user_id)
        
        if not success:
            # 🤫 S'il l'a déjà pris, on envoie un message 100% éphémère
            await interaction.response.send_message(
                f"❌ T'as déjà pris ton Wake & Bake aujourd'hui frérot ! Reviens demain. (Série en cours : **{streak} 🔥**)", 
                ephemeral=True
            )
            return
            
        # 🎉 S'il gagne, on donne les points et on fait une annonce publique !
        new_total = await database.add_points(database.db_pool, user_id, reward)
        await helpers.update_member_prestige_role(interaction.user, new_total)
        
        embed = discord.Embed(
            title="🌅 WAKE & BAKE",
            description=f"Bien le bonjour {interaction.user.mention} ! ☕🌿\n\n"
                        f"🎁 Cadeau quotidien : **+{reward} points**\n"
                        f"🔥 Série en cours : **{streak} jours**\n"
                        f"📈 Multiplicateur actuel : **x{multiplicateur:.1f}**\n\n"
                        f"💰 Nouveau solde : **{new_total} points**",
            color=discord.Color.green()
        )
        
        if multiplicateur >= 2.0:
            embed.set_footer(text="👑 MAXIMUM ATTEINT ! Reviens tous les jours pour conserver ton x2 !")
        else:
            embed.set_footer(text="⚠️ N'oublie pas de revenir demain pour faire monter ton multiplicateur !")
            
        await interaction.response.send_message(embed=embed)

    # ---------------------------------------
    # /douille (Roulette Russe Multijoueur)
    # ---------------------------------------
    @bot.tree.command(name="douille", description="Roulette Russe ! Jusqu'à 6 joueurs. 1 perdant, les autres raflent sa mise. 🔫")
    @app_commands.describe(mise="Nombre de points pour entrer dans la partie")
    async def douille(interaction: discord.Interaction, mise: int):
        if mise < 10:
            await interaction.response.send_message("❌ Minimum syndical : 10 points la partie.", ephemeral=True)
            return
            
        user_id = str(interaction.user.id)
        
        # Vérification des points (Mois + À Vie) pour le créateur
        current_points = await database.get_user_points(database.db_pool, user_id)
        monthly_points = await database.get_user_monthly_points(database.db_pool, user_id)
        solde_jouable = min(current_points, monthly_points)

        if solde_jouable < mise:
            await interaction.response.send_message(
                f"❌ T'es à sec frérot ! Tu dois parier un montant que tu possèdes sur les DEUX compteurs (Max jouable: **{solde_jouable}**).", 
                ephemeral=True
            )
            return
            
        view = DouilleView(interaction.user.id, mise)
        embed = discord.Embed(
            title="🔫 LA DOUILLE (Roulette Russe)",
            description=f"**Mise :** {mise} points\n**Joueurs (1/6) :**\n{interaction.user.mention}\n\n*Cliquez sur le bouton pour rejoindre ! Le coup part dans 60 secondes.*",
            color=discord.Color.dark_theme()
        )
        await interaction.response.send_message(embed=embed, view=view)
        
        # On attend 60 secondes OU que 6 joueurs soient là
        await view.wait()
        
        original_msg = await interaction.original_response()
        
        # On désactive le bouton une fois la partie lancée
        for child in view.children:
            child.disabled = True
        await original_msg.edit(view=view)
        
        if len(view.players) < 2:
            await interaction.followup.send("❌ Pas assez de couilles sur le serveur... La partie est annulée (il faut au moins 2 joueurs) !", ephemeral=False)
            return
            
        # Sécurité ultime : on revérifie les points juste avant le tirage au cas où un mec a dépensé ses points entre-temps
        final_players = []
        for pid in view.players:
            p_current = await database.get_user_points(database.db_pool, str(pid))
            p_monthly = await database.get_user_monthly_points(database.db_pool, str(pid))
            if min(p_current, p_monthly) >= mise:
                final_players.append(pid)
                
        if len(final_players) < 2:
            await interaction.followup.send("❌ Partie annulée : Certains petits malins ont dépensé leurs points avant le tirage.", ephemeral=False)
            return
            
        # 💥 LE TIRAGE FATAL
        loser_id = random.choice(final_players)
        winners = [pid for pid in final_players if pid != loser_id]
        
        # Le perdant perd toute sa mise, les gagnants se partagent sa mise
        gain_per_winner = mise // len(winners)
        
        # On retire les points du perdant
        await database.add_points(database.db_pool, str(loser_id), -mise)
        
        # On donne les points aux gagnants
        for wid in winners:
            await database.add_points(database.db_pool, str(wid), gain_per_winner)
            
        # Création du message de résultat
        loser_mention = f"<@{loser_id}>"
        winners_mentions = "\n".join([f"✅ <@{w}> (+{gain_per_winner} pts)" for w in winners])
        
        res_embed = discord.Embed(
            title="💥 PAN ! LE COUP EST PARTI !",
            description=f"Le barillet a tourné... Et c'est {loser_mention} qui se prend la douille dans la tête ! 💀\n\n"
                        f"💸 **Il perd sa mise de {mise} points.**\n\n"
                        f"🏆 **Les survivants se partagent le butin :**\n{winners_mentions}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=res_embed)
    # ---------------------------------------
    # /spawn (admin)
    # ---------------------------------------
    @bot.tree.command(name="spawn", description="Force le spawn immédiat d’un Pokéweed (admin only)")
    async def spawn_cmd(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        from . import tasks  # importe tes tâches (dont spawn_pokeweed)

        await interaction.response.defer(ephemeral=True)
        try:
            await tasks.spawn_pokeweed(bot)
            await interaction.followup.send("✅ Un Pokéweed vient de spawn dans le channel dédié !", ephemeral=True)
        except Exception as e:
            logger.exception("Erreur dans /spawn : %s", e)
            await interaction.followup.send(f"❌ Une erreur est survenue : {e}", ephemeral=True)

    @bot.tree.command(name="vibe-setup", description="(Admin) Publie le message de rôles (weed/shit) et pose les réactions")
    async def vibe_setup(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        channel = bot.get_channel(config.REACTION_ROLE_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("❌ Salon introuvable (vérifie REACTION_ROLE_CHANNEL_ID).", ephemeral=True)
            return

        guild = interaction.guild
        weed_role = guild.get_role(config.WEED_ROLE_ID)
        shit_role = guild.get_role(config.SHIT_ROLE_ID)
        if not weed_role or not shit_role:
            await interaction.response.send_message("❌ Rôle(s) introuvable(s) (vérifie WEED_ROLE_ID / SHIT_ROLE_ID).", ephemeral=True)
            return

        # Le message affiché
        lines = [
            "🥦 **Choisis ta vibe !** 🍫",
            "",
            "Impose ton choix, et montre à tout le monde ce que tu préfères 🧑‍🚀",
            "",
            f"{config.EMOJI_WEED} Team WEED → {weed_role.mention}",
            f"{config.EMOJI_SHIT} Team SHIT → {shit_role.mention}",
            "",
            "_Ajoute la réaction que tu souhaites pour **prendre** le rôle, retire-la pour **l’enlever** ✅ ._",
        ]
        await interaction.response.defer(ephemeral=True)
        message = await channel.send("\n".join(lines))

        # Ajoute les réactions
        for emoji in (config.EMOJI_WEED, config.EMOJI_SHIT):
            try:
                await message.add_reaction(emoji)
            except Exception:
                pass

        # Sauvegarde runtime + feedback
        from . import state
        state.weed_shit_message_id = message.id
        await interaction.followup.send(
            f"✅ Reaction roles prêts dans {channel.mention}.\nMessage ID: `{message.id}`",
            ephemeral=True
        )

    # ---------------------------------------
    # /candidature
    # ---------------------------------------
    @bot.tree.command(name="candidature", description="Postule pour rejoindre l'équipe du staff Kanaé !")
    async def candidature(interaction: discord.Interaction):
        # On affiche le formulaire à l'utilisateur
        await interaction.response.send_modal(CandidatureModal())

    # Fonctions d'autocomplétion pour la commande /echange
    async def poke_autocomplete_self(interaction: discord.Interaction, current: str):
        pokes = await database.get_user_pokeweeds_unique(database.db_pool, interaction.user.id)
        choices = []
        for pid, name, count in pokes:
            if current.lower() in name.lower():
                choices.append(app_commands.Choice(name=f"{name} (x{count})", value=str(pid)))
        return choices[:25]

    async def poke_autocomplete_other(interaction: discord.Interaction, current: str):
        # On récupère le membre sélectionné s'il existe déjà dans l'espace de noms
        target_id_str = getattr(interaction.namespace, 'membre', None)
        if not target_id_str:
            return []
            
        try:
            target_id = int(target_id_str)
            pokes = await database.get_user_pokeweeds_unique(database.db_pool, target_id)
            choices = []
            for pid, name, count in pokes:
                if current.lower() in name.lower():
                    choices.append(app_commands.Choice(name=f"{name} (x{count})", value=str(pid)))
            return choices[:25]
        except (ValueError, TypeError):
            return []

    # ---------------------------------------
    # /echange
    # ---------------------------------------
    @bot.tree.command(name="echange", description="Propose un échange de Pokéweed à un autre membre !")
    @app_commands.describe(
        membre="Avec qui veux-tu échanger ?",
        mon_pokeweed="La carte que TU donnes",
        son_pokeweed="La carte que TU veux"
    )
    @app_commands.autocomplete(mon_pokeweed=poke_autocomplete_self, son_pokeweed=poke_autocomplete_other)
    async def echange(interaction: discord.Interaction, membre: discord.Member, mon_pokeweed: str, son_pokeweed: str):
        if membre.id == interaction.user.id or membre.bot:
            await interaction.response.send_message("❌ Tu ne peux pas échanger avec toi-même ou avec un bot frérot.", ephemeral=True)
            return

        try:
            p1_id = int(mon_pokeweed)
            p2_id = int(son_pokeweed)
        except ValueError:
            await interaction.response.send_message("❌ Sélection invalide. Utilise les propositions de l'autocomplétion !", ephemeral=True)
            return

        # Double check serveur : Vérifier les quantités possédées à l'instant T
        c1 = await database.get_specific_pokeweed_count(database.db_pool, interaction.user.id, p1_id)
        c2 = await database.get_specific_pokeweed_count(database.db_pool, membre.id, p2_id)

        if c1 == 0:
            await interaction.response.send_message("❌ Tu ne possèdes plus cette carte !", ephemeral=True)
            return
        if c2 == 0:
            await interaction.response.send_message(f"❌ {membre.display_name} ne possède plus cette carte !", ephemeral=True)
            return

        # Récupération des noms pour l'affichage (via la base de données)
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT name FROM pokeweeds WHERE id=%s;", (p1_id,))
                p1_name = (await cur.fetchone())[0]
                await cur.execute("SELECT name FROM pokeweeds WHERE id=%s;", (p2_id,))
                p2_name = (await cur.fetchone())[0]

        # PRÉVISUALISATION (Message Éphémère)
        embed = discord.Embed(
            title="👀 Prévisualisation de l'échange",
            description="Vérifie bien les détails avant d'envoyer ta proposition sur le salon.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Tu donnes :", value=f"🌿 **{p1_name}**\n*(Il t'en restera {c1 - 1})*", inline=False)
        embed.add_field(name="Tu reçois :", value=f"🌿 **{p2_name}**\n*(Lui en restera {c2 - 1})*", inline=False)
        
        view = TradePreviewView(interaction.client, interaction.user, membre, p1_id, p2_id, p1_name, p2_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
