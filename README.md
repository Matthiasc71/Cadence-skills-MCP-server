# cadence-eda : marketplace Claude Code pour Cadence Design Systems

Ce dépôt est une **marketplace de plugins Claude Code**. Elle contient le plugin `cadence` :

- **Serveur MCP `cadence`** (`cadence_mcp` 0.2.0) : 36 outils `cadence_*` pour Virtuoso/SKILL/OCEAN, Spectre, Genus/Innovus/Tempus/Voltus,
  Xcelium et Allegro/OrCAD, sur Virtuoso IC6.1.7 → IC25.1 et les releases associées (détails :
  `plugins/cadence/server/README.md`).
- **Skill `cadence-eda`** : expertise multi-versions (ADE L/XL → maestro, Stylus vs legacy, Spectre X…),
  chargé automatiquement quand vous parlez de Cadence.
- **Commandes** : `/cadence:setup`, `/cadence:doctor [profil]`, `/cadence:cdsinit [id]`.

```text
.claude-plugin/marketplace.json        catalogue (marketplace "cadence-eda")
plugins/cadence/
├── .claude-plugin/plugin.json         manifeste + options (fichier de config, niveau d'accès, Python)
├── .mcp.json                          déclaration du serveur MCP
├── scripts/cadence-mcp-launch.sh      crée l'environnement Python au premier lancement, puis démarre le serveur
├── skills/cadence-eda/SKILL.md        le skill d'expertise
├── skills/{setup,doctor,cdsinit}/     les commandes /cadence:*
└── server/                            code du serveur (Python ≥ 3.10) + tests
```

## 1. Récupérer ou contribuer

Le dépôt est publié sur https://github.com/Matthiasc71/Cadence-skills-MCP-server. Pour le modifier :

```bash
git clone https://github.com/Matthiasc71/Cadence-skills-MCP-server.git
cd Cadence-skills-MCP-server
claude plugin validate .          # à relancer avant chaque push
```

Pour une copie sur un GitLab/Gitea interne, poussez ce même contenu vers un dépôt de votre serveur.

## 2. Installer le plugin (chaque utilisateur, sur la machine EDA)

```bash
claude plugin marketplace add Matthiasc71/Cadence-skills-MCP-server      # GitHub (owner/repo)
# ou : claude plugin marketplace add https://gitlab.interne/eda/Cadence-skills-MCP-server.git
# ou, pour tester sans dépôt : claude plugin marketplace add ./Cadence-skills-MCP-server
claude plugin install cadence@cadence-eda
```

Les mêmes opérations existent dans une session : `/plugin marketplace add …` puis `/plugin install cadence@cadence-eda`.

## 3. Première utilisation

Dans une session Claude Code :

```text
/cadence:setup       installe l'environnement Python du serveur et vérifie la configuration
/mcp                 reconnecte le serveur « cadence » si besoin
/cadence:doctor      liste les outils Cadence et leurs versions, par profil
/cadence:cdsinit     lignes à ajouter au ~/.cdsinit pour le mode Virtuoso « live »
```

Le serveur a besoin de **Python ≥ 3.10**. Sur RHEL/CentOS, `python3` est souvent trop ancien :
installez `python3.11` ou indiquez un interpréteur dans les options du plugin.

## 4. Configuration

Options du plugin (`/plugin` → Installed → cadence → Configure, ou `/config`) :

| Option | Effet |
|---|---|
| Fichier de configuration | Chemin du JSON des profils (voir `plugins/cadence/server/config.example.json`) |
| Niveau d'accès | `read`, `run` (défaut) ou `write` |
| Python ≥ 3.10 | Interpréteur utilisé pour l'environnement du serveur |

Une option vide laisse la place aux variables du shell (`CADENCE_MCP_CONFIG`, `CADENCE_MCP_ACCESS`,
`CADENCE_MCP_PYTHON`, et toutes les autres `CADENCE_MCP_*` décrites dans le README du serveur).

Exemple de fichier avec plusieurs versions installées côte à côte :

```json
{
  "default_profile": "ic231",
  "allowed_roots": ["~/projets"],
  "profiles": {
    "ic617": {"setup": "/eda/cadence/setup/IC617.csh"},
    "ic231": {"setup": "/eda/cadence/setup/IC231.sh"},
    "ic251": {"setup": "/eda/cadence/setup/IC251.sh"}
  },
  "virtuoso": {"project_dir": "~/projets/mon_asic"}
}
```

## 5. Évaluer

- Serveur : `plugins/cadence/server/evaluations/` contient 10 questions au format `mcp-builder`, sur un
  environnement de démonstration figé (voir son README).
- Skill : `plugins/cadence/skills/cadence-eda/evals/` contient 3 cas au format `claude plugin eval`. Ce sont
  de vrais runs de modèle, facturés :

```bash
cd plugins/cadence/skills/cadence-eda && claude plugin eval . --allow-tools Write,Edit,Bash --scaffold
```

## 6. Mettre à jour

Modifiez le dépôt, **augmentez `version`** dans `plugins/cadence/.claude-plugin/plugin.json`
(les utilisateurs restent sur une version tant qu'elle ne change pas), poussez, puis chaque utilisateur lance :

```bash
claude plugin marketplace update cadence-eda
claude plugin update cadence@cadence-eda
```

L'environnement Python du serveur se réinstalle tout seul au démarrage suivant.

## Variante : dans un dépôt de projet, sans marketplace

Pour qu'un seul dépôt de projet (par exemple celui de votre ASIC) embarque le skill et le serveur :

```bash
cd mon-projet
mkdir -p .claude/skills
cp -r /chemin/Cadence-skills-MCP-server/plugins/cadence/skills/cadence-eda .claude/skills/
python3.11 -m venv ~/venvs/cadence-mcp
~/venvs/cadence-mcp/bin/pip install "/chemin/Cadence-skills-MCP-server/plugins/cadence/server[virtuoso]"
claude mcp add --env CADENCE_MCP_CONFIG=$HOME/cadence_mcp.json --transport stdio --scope project cadence \
  -- $HOME/venvs/cadence-mcp/bin/cadence-mcp
git add .claude/skills/cadence-eda .mcp.json
git commit -m "Claude Code : skill et serveur MCP Cadence"
```

`--scope project` écrit le serveur dans `.mcp.json` à la racine du projet pour le partager avec
l'équipe. Le chemin du venv doit alors être le même pour tous, par exemple un venv partagé sur `/eda/tools`.
