# AnythingLLM Desktop Updater

> Petit utilitaire graphique **Windows** qui met à jour proprement l'application
> **AnythingLLM Desktop** lorsque le bouton « Update » intégré reste bloqué sur
> la même version.

Aucune installation compliquée, aucune ligne de commande : on **double-clique**
sur un fichier et tout se passe dans des fenêtres (pop-up) avec des boutons.

---

## 🧩 Le problème que cet outil résout

Quand on clique sur **Update** dans AnythingLLM Desktop, deux choses peuvent
mal tourner :

1. **Le cache.** Le bouton de mise à jour pointe toujours vers la **même URL** :
   `https://cdn.anythingllm.com/latest/AnythingLLMDesktop.exe`. Le nom de fichier
   ne changeant jamais, le navigateur (ou le cache du CDN) ressert souvent
   l'**ancien** installeur déjà téléchargé → on réinstalle la même version.

2. **L'incohérence entre le registre Windows et le disque.** C'est le cœur du
   bug. Une installation précédente interrompue peut écrire un numéro de version
   dans le **registre Windows** (ex. `1.13.0`) alors que les **fichiers réels**
   de l'application restent à une version antérieure (ex. `1.12.1`).

   | Source | Version annoncée |
   |---|---|
   | Registre Windows (ce que lit l'installeur) | `1.13.0` |
   | `AnythingLLM.exe` réel sur le disque | **`1.12.1`** |

   Résultat : l'installeur regarde le **registre**, croit que la dernière version
   est déjà présente, et affiche **« application déjà installée »**. Il refuse de
   mettre à jour, et l'application reste coincée sur l'ancienne version. Même en
   désinstallant/réinstallant, on retombe sur le même blocage.

### Comment l'outil corrige cela

- Il lit la **vraie** version directement depuis `AnythingLLM.exe` sur le disque,
  et **pas seulement** le registre. C'est la version fiable.
- Il **diagnostique et affiche** l'incohérence registre / disque quand elle existe.
- Il télécharge le dernier installeur en **contournant le cache** (paramètre
  unique + en-têtes `no-cache`) pour obtenir le **vrai** dernier `.exe`.
- Au moment d'installer, il lance le **véritable désinstalleur** (qui nettoie
  l'entrée de registre fautive), puis installe proprement la nouvelle version en
  mode **« utilisateur actuel »** (le seul mode supporté par AnythingLLM).

---

## ♾️ Universel et évolutif (pensé pour durer)

Aucun numéro de version n'est codé en dur. La comparaison est **purement
numérique**, composant par composant. L'outil reconnaîtra automatiquement
qu'une version est plus récente, quel que soit l'écart ou le format :

| Version installée | Version proposée | Reconnu comme « plus récent » ? |
|---|---|---|
| `1.12.1` | `1.13.0` | ✅ Oui |
| `1.12.1` | `2.0.0` | ✅ Oui (saut majeur) |
| `1.12.1` | `1.200.0` | ✅ Oui (200 versions d'écart) |
| `9.99.99` | `10.5.3` | ✅ Oui (deux chiffres) |
| `1.12.1` | `1.12.1.5` | ✅ Oui (composant supplémentaire) |
| `1.12.9` | `1.12.10` | ✅ Oui (comparaison numérique, pas alphabétique) |
| `1.12.1` | `1.12.1` | ❌ Non (déjà à jour) |

Vous pourrez donc toujours l'utiliser dans **plusieurs années**, sans
modification, pour les futures mises à jour.

---

## 🚀 Utilisation (simple, sans terminal)

### Prérequis
- **Windows** (10 ou 11)
- **Python 3** installé (tkinter est inclus de base avec Python sous Windows)

### Étapes
1. **Double-cliquez** sur le fichier **`AnythingLLM Desktop Updater.pyw`**.
   - L'extension `.pyw` lance le programme **sans fenêtre noire de terminal** :
     une simple fenêtre d'application s'ouvre.
2. La fenêtre affiche automatiquement la **vraie version installée** et signale
   toute incohérence détectée.
3. Cliquez sur **« Vérifier les mises à jour »**.
   - L'outil télécharge le dernier installeur officiel et compare les versions.
   - Une pop-up vous indique si vous êtes à jour ou si une nouvelle version
     existe.
4. Si une mise à jour est disponible, le bouton **« Installer la mise à jour »**
   se débloque. Cliquez dessus, confirmez, et laissez faire.

> 💾 **Vos données sont conservées.** L'outil ne touche jamais au dossier
> `%APPDATA%\anythingllm-desktop\storage`. Vos espaces de travail, vos
> documents et vos réglages restent intacts.

---

## 💡 Astuce : « Réessayer » si l'installation ne part pas du premier coup

Lors de la **toute première** tentative d'installation, il arrive que rien ne se
lance immédiatement et que l'installeur affiche un bouton **« Réessayer » /
« Retry »**.

C'est normal : juste après la désinstallation de l'ancienne version, certains
fichiers peuvent rester **verrouillés une seconde** par Windows. Il suffit de
**cliquer sur « Réessayer »** : la deuxième tentative démarre l'installation
correctement.

---

## 🔧 Fonctionnement technique (résumé)

| Étape | Détail |
|---|---|
| Détection | Parcourt le registre (`HKCU` + `HKLM`) et lit `AnythingLLM.exe` sur le disque |
| Comparaison | Conversion en tuple d'entiers + comparaison numérique (`is_newer`) |
| Téléchargement | URL officielle du CDN + cache-busting (`no-cache`) |
| Nettoyage | Lance le vrai désinstalleur (supprime l'entrée de registre fautive) |
| Installation | Relance l'installeur officiel en mode « utilisateur actuel » |

Architecture détectée automatiquement : **x64** ou **ARM64**.

---

## ⚠️ Avertissement

Ce projet est un **utilitaire communautaire non officiel**. Il n'est pas affilié
à AnythingLLM ni à Mintplex Labs. Il se contente de télécharger l'installeur
**officiel** depuis le CDN d'AnythingLLM et d'automatiser une procédure de
réinstallation propre. Utilisez-le à vos risques ; vos données ne sont pas
modifiées, mais une sauvegarde reste toujours une bonne pratique.

---

## 📄 Licence

Distribué sous licence **MIT**. Voir le fichier [LICENSE](LICENSE).
