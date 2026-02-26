import logging
import aiomysql
from datetime import date, datetime, timezone, timedelta

from . import config

logger = logging.getLogger(__name__)

db_pool = None

async def init_db_pool():
    try:
        pool = await aiomysql.create_pool(
            host=config.MYSQLHOST,
            port=config.MYSQLPORT,
            user=config.MYSQLUSER,
            password=config.MYSQLPASSWORD,
            db=config.MYSQLDATABASE,
            autocommit=True,
        )
        logger.info("DB pool created: %s@%s:%s/%s", config.MYSQLUSER, config.MYSQLHOST, config.MYSQLPORT, config.MYSQLDATABASE)
        return pool
    except Exception as e:
        logger.exception("Unable to create MySQL pool: %s", e)
        raise

async def ensure_tables(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS booster_cooldowns (
                    user_id BIGINT PRIMARY KEY,
                    last_opened DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )   
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scores (
                    user_id BIGINT PRIMARY KEY,
                    points INT NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_limits (
                    user_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    PRIMARY KEY(user_id, channel_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reaction_tracker (
                    message_id BIGINT NOT NULL,
                    reactor_id BIGINT NOT NULL,
                    PRIMARY KEY(message_id, reactor_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS news_history (
                    link VARCHAR(768) PRIMARY KEY,
                    date DATE NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS recap_history (
                    sent_date DATE PRIMARY KEY
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """CREATE TABLE IF NOT EXISTS thread_participation (
                    thread_id BIGINT,
                    user_id BIGINT,
                    PRIMARY KEY(thread_id, user_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

                CREATE TABLE IF NOT EXISTS thread_daily_creations (
                    user_id BIGINT,
                    date DATE,
                    PRIMARY KEY(user_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
                )
            # Twitch links table
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_links (
                    user_id BIGINT,
                    platform VARCHAR(50),
                    username VARCHAR(255),
                    PRIMARY KEY(user_id, platform),
                    UNIQUE(platform, username)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_rewards (
                    user_id BIGINT,
                    platform VARCHAR(50),
                    PRIMARY KEY(user_id, platform)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS twitch_sub_claims (
                    user_id BIGINT PRIMARY KEY,
                    last_claimed DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_account_rewards (
                    platform VARCHAR(50),
                    username VARCHAR(255),
                    PRIMARY KEY(platform, username)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_refresh (
                    user_id BIGINT PRIMARY KEY,
                    last_refresh DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )

            # Wake & Bake table
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS wake_and_bake (
                    user_id BIGINT PRIMARY KEY,
                    last_claim DATE,
                    streak INT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )

            # Pokeweed tables
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS pokeweeds (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100),
                    hp INT,
                    capture_points INT,
                    power INT,
                    rarity VARCHAR(50),
                    drop_rate FLOAT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_pokeweeds (
                    user_id BIGINT,
                    pokeweed_id INT,
                    capture_date DATETIME,
                    PRIMARY KEY (user_id, pokeweed_id, capture_date),
                    FOREIGN KEY (pokeweed_id) REFERENCES pokeweeds(id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            # Table pour l'historique des ventes de pokeweeds
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS pokeweed_sales (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    pokeweed_id INT,
                    points_earned INT,
                    sale_date DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            # Table pour les scores mensuels (pour le classement)
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_scores (
                    user_id BIGINT PRIMARY KEY,
                    points INT NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            # Table pour limiter les annonces de live (3 par semaine)
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS live_announcements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    announce_date DATETIME NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
    logger.info("Database tables checked/created")

async def get_user_points(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0] if row else 0

async def set_user_points(pool, user_id, pts, categorie="vie"):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if categorie == "vie":
                # On modifie uniquement le score global
                await cur.execute(
                    """
                    INSERT INTO scores (user_id, points) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE points = %s;
                    """,
                    (int(user_id), pts, pts),
                )
            elif categorie == "mois":
                # On modifie uniquement le score du mois
                await cur.execute(
                    """
                    INSERT INTO monthly_scores (user_id, points) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE points = %s;
                    """,
                    (int(user_id), pts, pts),
                )
            return pts

async def add_points(pool, user_id, pts):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. Ajout/Soustraction dans les scores À VIE (Bloqué à 0 minimum)
            await cur.execute(
                """
                INSERT INTO scores (user_id, points) VALUES (%s, GREATEST(0, %s))
                ON DUPLICATE KEY UPDATE points = GREATEST(0, CAST(points AS SIGNED) + %s);
                """,
                (int(user_id), pts, pts),
            )
            
            # 2. Ajout/Soustraction dans les scores MENSUELS (Bloqué à 0 minimum)
            await cur.execute(
                """
                INSERT INTO monthly_scores (user_id, points) VALUES (%s, GREATEST(0, %s))
                ON DUPLICATE KEY UPDATE points = GREATEST(0, CAST(points AS SIGNED) + %s);
                """,
                (int(user_id), pts, pts),
            )
            
            # On retourne toujours le score à vie pour les rôles de prestige
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0]

async def reset_monthly_scores(pool):
    """Remet tous les compteurs du mois à zéro."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE monthly_scores SET points = 0;")

async def get_user_monthly_points(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT points FROM monthly_scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0] if row else 0

async def has_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT 1 FROM daily_limits
                WHERE user_id=%s AND channel_id=%s AND date=%s;
                """,
                (int(user_id), int(channel_id), date),
            )
            return await cur.fetchone() is not None

async def set_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO daily_limits (user_id, channel_id, date)
                VALUES (%s, %s, %s);
                """,
                (int(user_id), int(channel_id), date),
            )

async def has_reaction_been_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT 1 FROM reaction_tracker
                WHERE message_id=%s AND reactor_id=%s;
                """,
                (int(message_id), int(reactor_id)),
            )
            return await cur.fetchone() is not None

async def set_reaction_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO reaction_tracker (message_id, reactor_id)
                VALUES (%s, %s);
                """,
                (int(message_id), int(reactor_id)),
            )

async def has_sent_news(pool, link):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM news_history WHERE link=%s;", (link,))
            return await cur.fetchone() is not None

async def mark_news_sent(pool, link, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO news_history (link, date)
                VALUES (%s, %s);
                """,
                (link, date),
            )

async def has_sent_recap(pool, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM recap_history WHERE sent_date=%s;",
                (date,),
            )
            return await cur.fetchone() is not None

async def mark_recap_sent(pool, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO recap_history (sent_date) VALUES (%s);",
                (date,),
            )

async def get_last_recap_date(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT MAX(sent_date) FROM recap_history;")
            row = await cur.fetchone()
            return row[0] if row and row[0] else None

# Twitch account linking functions
async def get_social_by_discord(pool, user_id, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT username FROM social_links WHERE user_id = %s AND platform = %s;",
                (int(user_id), platform)
            )
            row = await cur.fetchone()
            return row[0] if row else None

async def get_discord_by_social(pool, username, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM social_links WHERE username = %s AND platform = %s;",
                (username, platform)
            )
            row = await cur.fetchone()
            return row[0] if row else None

async def link_social_account(pool, user_id, platform, username):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. Vérifie si le pseudo est pris par un AUTRE joueur sur cette plateforme
            await cur.execute(
                "SELECT user_id FROM social_links WHERE platform = %s AND username = %s;",
                (platform, username)
            )
            row = await cur.fetchone()
            if row and row[0] != int(user_id):
                return False

            # 2. Ajoute ou met à jour le lien
            await cur.execute(
                """
                INSERT INTO social_links (user_id, platform, username) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE username = %s;
                """,
                (int(user_id), platform, username, username),
            )
            return True

async def unlink_social_account(pool, user_id, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM social_links WHERE user_id = %s AND platform = %s;",
                (int(user_id), platform)
            )

async def check_and_reward_social_link(pool, user_id, platform, username):
    # Double sécurité : On vérifie le compte Discord ET le pseudo Twitch
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. Est-ce que ce compte Discord a déjà eu les points pour ce réseau ?
            await cur.execute("SELECT 1 FROM social_rewards WHERE user_id = %s AND platform = %s;", (int(user_id), platform))
            discord_used = await cur.fetchone()
            
            # 2. Est-ce que ce pseudo Twitch a déjà été utilisé par quelqu'un pour gratter les points ?
            await cur.execute("SELECT 1 FROM social_account_rewards WHERE platform = %s AND username = %s;", (platform, username))
            social_used = await cur.fetchone()
            
            # Si ni le Discord ni le Twitch n'ont été utilisés, on donne les points !
            if not discord_used and not social_used:
                # On verrouille le Discord
                await cur.execute("INSERT INTO social_rewards (user_id, platform) VALUES (%s, %s);", (int(user_id), platform))
                # On verrouille le compte Twitch !
                await cur.execute("INSERT INTO social_account_rewards (platform, username) VALUES (%s, %s);", (platform, username))
                return True
                
            return False
        
async def claim_twitch_sub_reward(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT last_claimed FROM twitch_sub_claims WHERE user_id = %s;", (int(user_id),))
            row = await cur.fetchone()
            
            now = datetime.now(timezone.utc)
            
            if row and row[0]:
                last_claimed = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                # On vérifie si 28 jours se sont écoulés depuis le dernier claim de sub
                if now - last_claimed < timedelta(days=28):
                    return False # Trop tôt, il a déjà pris ses points pour ce mois-ci
            
            # S'il n'a jamais claim, ou si ça fait + de 28 jours : on met à jour la date et on l'autorise
            await cur.execute(
                """
                INSERT INTO twitch_sub_claims (user_id, last_claimed) 
                VALUES (%s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE last_claimed = UTC_TIMESTAMP();
                """,
                (int(user_id),)
            )
            return True
        
async def get_all_socials_by_discord(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT platform, username FROM social_links WHERE user_id = %s;",
                (int(user_id),)
            )
            return await cur.fetchall()
        
async def claim_wake_and_bake(pool, user_id):
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # On vérifie la dernière fois qu'il a claim
            await cur.execute("SELECT last_claim, streak FROM wake_and_bake WHERE user_id = %s;", (int(user_id),))
            row = await cur.fetchone()

            if row:
                last_claim, streak = row
                if last_claim == today:
                    return False, streak, 0, 1.0 # Déjà claim aujourd'hui
                
                # S'il a claim hier, on augmente la série, sinon on remet à 1
                if last_claim == today - timedelta(days=1):
                    streak += 1
                else:
                    streak = 1
            else:
                streak = 1

            # --- CALCUL DU MULTIPLICATEUR ---
            # Jour 1 = x1.0 | Jour 2 = x1.1 | Jour 3 = x1.2 etc...
            multiplicateur = 1.0 + ((streak - 1) * 0.1)
            
            # On bloque au maximum à x2.0 (atteint au bout de 11 jours)
            if multiplicateur > 2.0:
                multiplicateur = 2.0

            base_reward = 50 # Le gain de base
            final_reward = round(base_reward * multiplicateur)

            # On met à jour la base de données
            await cur.execute(
                """
                INSERT INTO wake_and_bake (user_id, last_claim, streak) 
                VALUES (%s, %s, %s) 
                ON DUPLICATE KEY UPDATE last_claim=%s, streak=%s;
                """,
                (int(user_id), today, streak, today, streak)
            )

            return True, streak, final_reward, multiplicateur
        
async def get_recent_sales_count(pool, user_id, hours=5):
    """Compte combien de ventes l'utilisateur a fait dans les X dernières heures."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM pokeweed_sales WHERE user_id=%s AND sale_date >= DATE_SUB(NOW(), INTERVAL %s HOUR);",
                (int(user_id), int(hours))
            )
            row = await cur.fetchone()
            return row[0] if row else 0

async def sell_pokeweed(pool, user_id, pokeweed_id, points):
    """Supprime UNE copie exacte de la carte et enregistre la vente pour limiter la fraude."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. On cherche la date d'une copie pour être sûr de n'en supprimer qu'UNE seule
            await cur.execute(
                "SELECT capture_date FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s LIMIT 1;",
                (int(user_id), int(pokeweed_id))
            )
            row = await cur.fetchone()
            if not row:
                return False # Il n'a pas (ou plus) la carte

            capture_date = row[0]
            
            # 2. On supprime cette copie précise
            await cur.execute(
                "DELETE FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s AND capture_date=%s LIMIT 1;",
                (int(user_id), int(pokeweed_id), capture_date)
            )
            if cur.rowcount == 0:
                return False

            # 3. On enregistre la vente dans l'historique
            await cur.execute(
                "INSERT INTO pokeweed_sales (user_id, pokeweed_id, points_earned, sale_date) VALUES (%s, %s, %s, NOW());",
                (int(user_id), int(pokeweed_id), int(points))
            )

    # 4. On crédite les points via ta fonction existante
    await add_points(pool, user_id, points)
    return True

async def get_weekly_live_count(pool, user_id):
    """Vérifie combien de lives ont été annoncés dans les 7 derniers jours."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM live_announcements WHERE user_id=%s AND announce_date >= DATE_SUB(NOW(), INTERVAL 7 DAY);",
                (int(user_id),)
            )
            row = await cur.fetchone()
            return row[0] if row else 0

async def add_live_announcement(pool, user_id):
    """Enregistre une nouvelle annonce de live."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO live_announcements (user_id, announce_date) VALUES (%s, NOW());",
                (int(user_id),)
            )

async def get_user_pokeweeds_unique(pool, user_id):
    """Récupère la liste des cartes uniques d'un joueur avec la rareté pour l'autocomplétion"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # On ajoute p.rarity ici !
            await cur.execute("""
                SELECT p.id, p.name, p.rarity, COUNT(*) as total 
                FROM user_pokeweeds up 
                JOIN pokeweeds p ON up.pokeweed_id = p.id 
                WHERE up.user_id=%s 
                GROUP BY p.id;
            """, (int(user_id),))
            return await cur.fetchall()

async def get_specific_pokeweed_count(pool, user_id, pokeweed_id):
    """Compte combien d'exemplaires d'une carte possède un joueur"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s;", (int(user_id), int(pokeweed_id)))
            row = await cur.fetchone()
            return row[0] if row else 0

async def execute_trade(pool, u1_id, p1_id, u2_id, p2_id):
    """Transaction SQL sécurisée : Échange les 2 cartes. Renvoie True si succès, False si triche."""
    async with pool.acquire() as conn:
        # On démarre une transaction stricte
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                # 1. On cherche la copie exacte (via sa date) du Joueur 1
                await cur.execute("SELECT capture_date FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s LIMIT 1 FOR UPDATE;", (int(u1_id), int(p1_id)))
                row1 = await cur.fetchone()
                if not row1:
                    await conn.rollback()
                    return False
                date1 = row1[0]

                # 2. On cherche la copie exacte du Joueur 2
                await cur.execute("SELECT capture_date FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s LIMIT 1 FOR UPDATE;", (int(u2_id), int(p2_id)))
                row2 = await cur.fetchone()
                if not row2:
                    await conn.rollback()
                    return False
                date2 = row2[0]

                # 3. On supprime les anciennes copies
                await cur.execute("DELETE FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s AND capture_date=%s LIMIT 1;", (int(u1_id), int(p1_id), date1))
                await cur.execute("DELETE FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s AND capture_date=%s LIMIT 1;", (int(u2_id), int(p2_id), date2))

                # 4. On insère les nouvelles cartes en croisant les proprios
                await cur.execute("INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());", (int(u2_id), int(p1_id)))
                await cur.execute("INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());", (int(u1_id), int(p2_id)))

            # Si on arrive ici sans erreur, on valide tout d'un coup !
            await conn.commit()
            return True
        except Exception as e:
            await conn.rollback()
            logger.error(f"Erreur transaction échange : {e}")
            return False