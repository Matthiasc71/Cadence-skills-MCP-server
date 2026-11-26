---
name: setup
description: Installe ou réinstalle l'environnement Python du serveur MCP cadence, puis vérifie les outils Cadence détectés.
disable-model-invocation: true
---

Prépare le serveur MCP `cadence` sur cette machine.

1. Lance l'installation (elle crée l'environnement Python du serveur, ou le met à jour) :

   ```bash
   CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" bash "${CLAUDE_PLUGIN_ROOT}/scripts/cadence-mcp-launch.sh" --install-only
   ```

   Si elle échoue parce que Python >= 3.10 est introuvable, explique à l'utilisateur comment
   indiquer un interpréteur : champ « Python >= 3.10 » de la configuration du plugin (`/plugin`,
   onglet Installed, cadence, Configure) ou variable `CADENCE_MCP_PYTHON`.

2. Vérifie la configuration sans lancer les outils Cadence :

   ```bash
   CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}" CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" bash "${CLAUDE_PLUGIN_ROOT}/scripts/cadence-mcp-launch.sh" doctor --fast
   ```

3. Résume en quelques lignes : niveau d'accès, profils, outils trouvés et manquants. Si aucun outil
   Cadence n'est trouvé, propose de créer un fichier de configuration à partir de
   `${CLAUDE_PLUGIN_ROOT}/server/config.example.json` avec un profil par installation, puis de
   renseigner son chemin dans la configuration du plugin.

4. Termine en rappelant de lancer `/mcp` pour reconnecter le serveur `cadence` si sa connexion
   avait échoué avant l'installation.
