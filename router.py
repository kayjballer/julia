import re
CODE = re.compile(r"\b(code|coder|python|java|javascript|html|script|fonction|bug|programme|programmer|termux|github)\b", re.I)
CMD = re.compile(r"\b(ouvre|ouvrir|lance|lancer|allume|allumer|eteins|éteins|eteindre|éteindre|reveil|réveil|reveille|réveille|appelle|appeler|envoie|envoyer|active|desactive|ferme|fermer|minuteur|alarme|rappel|volume|torche|wifi|bluetooth)\b", re.I)


def role(text):
    t = text.lower()
    if CODE.search(t):
        return 'code'
    if CMD.search(t) and len(t.split()) <= 8:
        return 'commande'
    return 'discussion'
