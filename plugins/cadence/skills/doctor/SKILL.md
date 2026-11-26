---
name: doctor
description: Diagnostic complet de l'installation Cadence vue par le serveur MCP (outils, versions par profil, configuration).
disable-model-invocation: true
argument-hint: "[profil]"
---

Diagnostique l'installation Cadence.

Lance la commande suivante. Si l'utilisateur a donné un profil ($ARGUMENTS), ajoute `--profile <profil>` :

```bash
CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" bash "${CLAUDE_PLUGIN_ROOT}/scripts/cadence-mcp-launch.sh" doctor
```

Les requêtes de version peuvent prendre jusqu'à une minute par outil (vérification de licence).

Présente ensuite un tableau par profil : outil, chemin, version détectée, et les notes de version
importantes (ADE L/XL retirés en IC23.1, Spectre X à partir de 19.1, UI par défaut d'Innovus/Genus).
Signale les outils manquants et les versions non reconnues, avec la piste de correction (script de
setup du profil, `CADENCE_MCP_BIN_<OUTIL>`).
