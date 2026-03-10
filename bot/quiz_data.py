# bot/quiz_data.py

QUIZ_QUESTIONS = [
    # --- BOTANIQUE & SCIENCE ---
    {"question": "Quel est le principal composant psychoactif de la weed ?", "options": ["CBD", "THC", "CBG", "CBN"], "answer": 1, "category": "Science 🔬"},
    {"question": "Comment s'appellent les petits cristaux brillants sur les têtes qui contiennent les cannabinoïdes ?", "options": ["Les pollens", "Les pistils", "Les trichomes", "Les terpènes"], "answer": 2, "category": "Botanique 🌿"},
    {"question": "Quel terpène donne à la weed son odeur de citron/agrumes ?", "options": ["Myrcène", "Linalol", "Pinène", "Limonène"], "answer": 3, "category": "Botanique 🌿"},
    {"question": "Laquelle de ces variétés est réputée pour donner un effet 'High' et énergisant ?", "options": ["Indica", "Sativa", "Ruderalis", "Afghan"], "answer": 1, "category": "Botanique 🌿"},
    {"question": "Comment appelle-t-on une plante de weed mâle qui a pollinisé une femelle ?", "options": ["Hermaphrodite", "Hermès", "Hybride", "Herma-G"], "answer": 0, "category": "Botanique 🌿"},

    # --- CULTURE 420 & HISTOIRE ---
    {"question": "D'où vient l'expression '420' ?", "options": ["Un code de la police californienne", "L'heure à laquelle des étudiants se retrouvaient", "Le nombre de composants chimiques", "Un anniversaire de Bob Marley"], "answer": 1, "category": "Culture 420 💨"},
    {"question": "Dans quel pays se trouve la ville d'Amsterdam, capitale européenne des Coffee Shops ?", "options": ["Belgique", "Allemagne", "Pays-Bas", "Danemark"], "answer": 2, "category": "Culture 420 💨"},
    {"question": "Quel est le vrai nom de Snoop Dogg ?", "options": ["Calvin Cordozar Broadus Jr.", "O'Shea Jackson", "Tupac Amaru Shakur", "Christopher George Latore Wallace"], "answer": 0, "category": "Culture 420 💨"},
    {"question": "Comment s'appelle la poudre résineuse récupérée au fond d'un grinder ?", "options": ["Le Shit", "Le Skuff (Kief)", "Le BHO", "Le Rosin"], "answer": 1, "category": "Culture 420 💨"},
    {"question": "En quelle année le Canada a-t-il légalisé la weed au niveau fédéral ?", "options": ["2014", "2016", "2018", "2020"], "answer": 2, "category": "Histoire 📜"},

    # --- FILMS & SÉRIES ---
    {"question": "Dans le film 'How High', avec quoi Silas et Jamal fertilisent-ils leur plant de weed ?", "options": ["Des cendres de leur pote Ivory", "Du sang de chauve-souris", "De l'eau bénite", "De l'engrais radioactif"], "answer": 0, "category": "Films & Séries 🎬"},
    {"question": "Quel duo de comiques est célèbre pour ses films de stoners dans les années 70/80 ?", "options": ["Key & Peele", "Cheech & Chong", "Jay & Silent Bob", "Harold & Kumar"], "answer": 1, "category": "Films & Séries 🎬"},
    {"question": "Dans 'Pineapple Express', quel acteur joue le rôle du dealer Saul Silver ?", "options": ["Seth Rogen", "Jonah Hill", "James Franco", "Danny McBride"], "answer": 2, "category": "Films & Séries 🎬"},
    {"question": "Comment s'appelle le personnage principal de 'The Big Lebowski', grand amateur de weed et de bowling ?", "options": ["Le Dude", "Le King", "Le Boss", "Le Chief"], "answer": 0, "category": "Films & Séries 🎬"},
    {"question": "Dans quelle série Walter White vend-il de la meth, mais où son associé Jesse Pinkman préfère largement la weed ?", "options": ["Narcos", "Peaky Blinders", "Breaking Bad", "Weeds"], "answer": 2, "category": "Films & Séries 🎬"},

    # --- RAP & MUSIQUE ---
    {"question": "Quel rappeur a sorti sa propre marque d'accessoires fumeurs appelée 'G-Pen' ?", "options": ["50 Cent", "Wiz Khalifa", "Snoop Dogg", "Eminem"], "answer": 0, "category": "Musique 🎵"},
    {"question": "Quel groupe de rap américain a sorti le hit 'Hits from the Bong' ?", "options": ["Wu-Tang Clan", "Outkast", "Cypress Hill", "Mobb Deep"], "answer": 2, "category": "Musique 🎵"},
    {"question": "Dans la culture Reggae/Rasta, comment appelle-t-on la weed ?", "options": ["La Ganja", "La Mary Jane", "L'Herbe", "Toutes ces réponses"], "answer": 3, "category": "Musique 🎵"},
    {"question": "Quel rappeur français a popularisé l'expression 'Pute Pute Pute' tout en allumant d'immenses joints dans ses clips ?", "options": ["Booba", "Alkpote", "Kaaris", "Jul"], "answer": 1, "category": "Musique 🎵"},
    {"question": "Qui a sorti l'album 'Rolling Papers' en 2011, devenu un classique pour fumer ?", "options": ["Snoop Dogg", "Curren$y", "Wiz Khalifa", "Kid Cudi"], "answer": 2, "category": "Musique 🎵"},
    
    # --- JEUX VIDÉOS & INTERNET ---
    {"question": "Dans GTA San Andreas, quel personnage te fait faire des missions en fumant constamment ?", "options": ["Big Smoke", "Ryder", "Sweet", "The Truth"], "answer": 3, "category": "Jeux Vidéos 🎮"},
    {"question": "Quel est le nom du Pokémon Plante souvent associé à la weed sur internet ?", "options": ["Boustiflor", "Bulbizarre", "Mystherbe", "Florizarre"], "answer": 2, "category": "Jeux Vidéos 🎮"},
    {"question": "Dans Minecraft, quelle est l'utilité des cisailles sur des feuilles ?", "options": ["Faire du shit", "Récupérer des feuilles", "Couper du bois", "Rien"], "answer": 1, "category": "Jeux Vidéos 🎮"},
    {"question": "Quel streamer/youtubeur français est connu pour son amour du jardinage et sa chaîne 'Les Frères Poulain' ?", "options": ["Antoine Daniel", "Joueur du Grenier", "Terracid", "Amixem"], "answer": 2, "category": "Internet 🌐"},
    {"question": "Dans League of Legends, comment s'appelle le champion qui est un arbre géant ?", "options": ["Ivern", "Maokai", "Groot", "Sylas"], "answer": 1, "category": "Jeux Vidéos 🎮"},
]