---
name: cdsinit
description: Affiche les lignes à ajouter au .cdsinit (ou à coller dans le CIW) pour connecter Virtuoso au serveur MCP via skillbridge.
disable-model-invocation: true
argument-hint: "[id de session]"
---

Génère les lignes de démarrage du pont skillbridge pour Virtuoso.

Lance la commande suivante. Si l'utilisateur a donné un identifiant de session ($ARGUMENTS),
ajoute `--id <identifiant>` :

```bash
CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" bash "${CLAUDE_PLUGIN_ROOT}/scripts/cadence-mcp-launch.sh" cdsinit
```

Montre les lignes obtenues dans un bloc de code, puis explique :

- les ajouter à la fin de `~/.cdsinit` pour un démarrage automatique, ou les coller dans le CIW
  d'une session déjà ouverte ;
- le Python indiqué par `?python` doit être accessible depuis la machine qui exécute Virtuoso ;
- avec plusieurs sessions Virtuoso, chacune a besoin d'un `?id` différent, puis
  `virtuoso_connect(workspace_id=...)` choisit la session ;
- tester ensuite avec l'outil `virtuoso_info` (le champ `mode` doit valoir `live`).

Ne modifie pas `~/.cdsinit` toi-même sans l'accord explicite de l'utilisateur.
