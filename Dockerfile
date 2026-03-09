# 1. On utilise une image Python officielle et légère (version 3.11 recommandée pour discord.py)
FROM python:3.11-slim

# 2. On empêche Python de créer des fichiers .pyc inutiles et on force l'affichage direct des logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. On définit le dossier de travail dans le conteneur
WORKDIR /app

# 4. On copie d'abord le fichier requirements pour installer les dépendances
COPY requirements.txt .

# 5. On met à jour pip et on installe les modules (sans garder le cache pour alléger l'image)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. On copie tout le reste de ton code dans le conteneur
COPY . .

# 7. La commande magique qui lance ton bot
CMD ["python", "mainNew.py"]