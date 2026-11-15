import re
import random

CODE = re.compile(r"\b(code|coder|python|java|javascript|html|script|fonction|bug|programme|programmer|termux|github)\b", re.I)
CMD = re.compile(r"\b(ouvre|ouvrir|lance|lancer|appelle|appeler|envoie|envoyer|active|desactive|ferme|fermer|minuteur|alarme|rappel|volume|torche|wifi|bluetooth)\b", re.I)


def role(text):
    t = text.lower()
    if CODE.search(t):
        return 'code'
    if CMD.search(t) and len(t.split()) <= 8:
        return 'commande'
    return 'discussion'


NOM_FAUX = ('goûts', 'gouts', 'négro', 'negro', 'nico', 'nicko', 'mico')
SALUTS = ('bonjour', 'salut', 'coucou', 'hello', 'bonsoir', 'hey', 'dis', 'ok', 'merci')


def fix_name(text):
    w = text.split()
    for i in range(min(2, len(w))):
        if i == 0 or w[i - 1].lower() in SALUTS:
            if w[i].lower().strip(',.!?') in NOM_FAUX:
                w[i] = 'Niko'
    return ' '.join(w)


FIN_OUVERTE = ('le', 'la', 'les', 'un', 'une', 'de', 'des', 'du', 'et', 'que', 'qui', 'à', 'pour', 'quoi', 'comment')


def coupee(text):
    w = text.lower().split()
    return bool(w) and w[-1].strip(',.?!') in FIN_OUVERTE


Q_EMO = re.compile(r"\b(triste|heureux|content|peur|stress|stressé|fatigué|déprimé|ça va)\b", re.I)
Q_AVIS = re.compile(r"\b(tu penses|ton avis|tu crois|tu préfères|meilleur|mieux)\b", re.I)
Q_SAVOIR = re.compile(r"(c'est quoi|qu'est[- ]ce|qui est|c'est qui|quand|combien|où|comment|pourquoi|explique)", re.I)
Q_SALUT = re.compile(r"\b(bonjour|salut|coucou|hello|bonsoir|merci)\b", re.I)


def qtype(text):
    t = text.lower()
    r = role(t)
    if r != 'discussion':
        return r
    if Q_EMO.search(t):
        return 'emotion'
    if Q_AVIS.search(t):
        return 'avis'
    if Q_SAVOIR.search(t):
        return 'savoir'
    if Q_SALUT.search(t) and len(t.split()) <= 4:
        return 'salut'
    return 'autre'


# (hauteur, vitesse, amorces dites pendant que le modele reflechit)
VOIX = {
    'salut': (1.15, 1.10, ('Salut !', 'Hey !')),
    'savoir': (1.00, 1.05, ('Alors...', 'Voyons...', 'Bonne question.')),
    'avis': (0.90, 0.95, ('Hmm...', 'Bon...')),
    'emotion': (0.88, 0.90, ('Hmm.', 'Je vois.')),
    'commande': (1.05, 1.15, ("D'accord.", 'Ok.')),
    'code': (0.95, 1.00, ('Voyons ça.',)),
    'autre': (1.00, 1.05, ()),
}


def amorce(kind):
    p, v, a = VOIX.get(kind, VOIX['autre'])
    return p, v, (random.choice(a) if a else '')
