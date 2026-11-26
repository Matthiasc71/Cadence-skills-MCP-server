# cadence-mcp

Serveur MCP (Model Context Protocol) qui donne à Claude l'accès aux outils **Cadence Design Systems**,
pour toutes les versions de **Virtuoso IC6.1.7 à IC25.1** et les releases Spectre, numériques et PCB
de la même période.

| Domaine | Outils Cadence | Outils MCP |
|---|---|---|
| Commun | – | `cadence_get_environment`, `cadence_list_profiles`, `cadence_get_compatibility_matrix`, `cadence_list_runs`, `cadence_get_run`, `cadence_cancel_run`, `cadence_find_files`, `cadence_read_text_file` |
| Custom IC | Virtuoso, SKILL, OCEAN | `cadence_virtuoso_connect`, `cadence_virtuoso_get_info`, `cadence_skill_eval`, `cadence_skill_load_file`, `cadence_skill_lint`, `cadence_virtuoso_list_libraries`, `cadence_virtuoso_list_cells`, `cadence_virtuoso_get_cellview`, `cadence_virtuoso_get_current_cellview`, `cadence_virtuoso_check_schematic`, `cadence_virtuoso_read_ciw_log`, `cadence_ocean_run_script`, `cadence_virtuoso_create_netlist` |
| Simulation analogique | Spectre, APS, Spectre X | `cadence_spectre_get_info`, `cadence_spectre_run`, `cadence_spectre_summarize_log`, `cadence_psf_list_results`, `cadence_psf_read` |
| Numérique | Genus, Innovus, Tempus, Voltus, Joules | `cadence_digital_get_info`, `cadence_digital_run_tcl`, `cadence_digital_summarize_log`, `cadence_digital_parse_report` |
| Vérification | Xcelium (xrun), Incisive (irun) | `cadence_xcelium_run`, `cadence_xcelium_summarize_log` |
| PCB | Allegro X / PCB Editor, OrCAD X Capture | `cadence_pcb_get_info`, `cadence_allegro_run_skill`, `cadence_allegro_get_board_summary`, `cadence_capture_run_tcl` |

Conventions (méthode `mcp-builder`) :
- serveur `cadence_mcp`, outils nommés `cadence_<domaine>_<action>` ;
- annotations complètes sur chaque outil (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) ;
- paramètres validés et décrits avec Pydantic (bornes, valeurs permises, exemples) ;
- `response_format` : `markdown` (défaut, compact) ou `json` (tous les champs) ;
- listes paginées : `limit`/`offset`, et la réponse donne `total`, `count`, `has_more` et `next_offset` ;
- erreurs renvoyées comme erreurs d'outil, avec l'action à faire (chemin hors racines, niveau d'accès, run inconnu…) ;
- un `run_id` unique (le nom du répertoire de run) sert aux runs en cours comme aux runs passés.

Ressources MCP : `cadence://compatibility` (matrice des versions) et `cadence://skill-helpers` (code SKILL chargé dans Virtuoso).

## Comment les versions sont gérées

1. **Profils** : un profil = une installation Cadence (script de setup `.sh`/`.csh`/`.bat` sourcé une fois,
   environnement mis en cache). Chaque outil accepte `profile="ic617"`, `"ic231"`… : le même script peut
   être lancé sur IC6.1.7 puis sur IC25.1.
2. **Détection à l'exécution** : `cadence_virtuoso_get_info` sonde les fonctions avec `isCallable()` (maestro `mae*`,
   ADE XL `axl*`, ADE L `sev*`, `schCheck`, `sklint`…) ; `cadence_digital_run_tcl` détecte le dialecte Stylus ou legacy
   du script et ajoute `-stylus` (Innovus/Tempus/Voltus) ou `-legacy_ui` (Genus) au besoin.
3. **Adaptation par version** : Spectre X (`+preset=`) n'est utilisé qu'à partir de SPECTRE 19.1 (sinon `+aps`),
   `xrun` est remplacé par `irun` sur les installations pré-Xcelium, le nombre de CPU est réglé avec la
   commande du bon dialecte.
4. **Code SKILL portable** : le fichier `skill/cadence_mcp.il` n'utilise que du SKILL de base et des fonctions
   dd*/db*/sch* présentes depuis IC6.1 ; tout le reste est protégé par `isCallable`.

## Prérequis

- Python **≥ 3.10** pour le serveur (SDK MCP 1.x ou 2.x, les deux sont pris en charge).
- Mode Virtuoso **live** : `skillbridge` (Virtuoso ≥ IC6.1.7). Le Python lancé par Virtuoso pour le pont doit être ≥ 3.6.
- Mode Virtuoso **batch** : seulement `virtuoso` (ou `ocean`) dans le PATH du profil et une licence.
- Le serveur tourne sur la machine (ou le cluster) où Cadence est installé, sous le compte du concepteur.

## Installation

```bash
python3.11 -m venv ~/venvs/cadence-mcp
~/venvs/cadence-mcp/bin/pip install "./cadence-mcp[virtuoso]"
cp cadence-mcp/config.example.json ~/cadence_mcp.json   # puis adaptez les profils
~/venvs/cadence-mcp/bin/cadence-mcp --config ~/cadence_mcp.json doctor   # vérifie outils et versions
```

### Côté Virtuoso (mode live)

```bash
~/venvs/cadence-mcp/bin/cadence-mcp cdsinit
```

affiche les trois lignes à ajouter dans `~/.cdsinit` (ou à coller dans le CIW) : chargement de skillbridge,
`pyStartServer(...)` et chargement des helpers SKILL. Avec plusieurs sessions Virtuoso, donnez à chacune
un `?id` différent et utilisez `cadence_virtuoso_connect(workspace_id=...)`.

## Brancher le serveur sur Claude

**Claude Code** (sur la machine EDA) :

```bash
claude mcp add --env CADENCE_MCP_CONFIG=$HOME/cadence_mcp.json --transport stdio --scope user cadence \
  -- $HOME/venvs/cadence-mcp/bin/cadence-mcp
```

**Claude Desktop** (`claude_desktop_config.json`), serveur EDA distant via SSH :

```json
{
  "mcpServers": {
    "cadence": {
      "command": "ssh",
      "args": ["-T", "serveur-eda", "env CADENCE_MCP_CONFIG=~/cadence_mcp.json ~/venvs/cadence-mcp/bin/cadence-mcp"]
    }
  }
}
```

`env` fonctionne aussi quand le shell de login est csh/tcsh, fréquent sur les serveurs EDA.
Le transport HTTP existe (`cadence-mcp serve --transport streamable-http --port 8765`) mais n'a pas
d'authentification : ne l'exposez qu'en local ou derrière un tunnel SSH.

## Niveaux d'accès et garde-fous

| Niveau | Autorise |
|---|---|
| `read` | Inspection : versions, bibliothèques, cellviews, logs, résultats PSF |
| `run` (défaut) | + simulations et scripts batch (Spectre, OCEAN, Genus/Innovus/Tempus, xrun, Allegro) dans un répertoire de run dédié |
| `write` | + modifications de la base (`dbSave`, `dbCreate*`, `schCheck`, affectations `obj~>attr = …`) |

- `allow_shell` (désactivé par défaut) contrôle `system()`, `sh()`, `ipcBeginProcess`, `exec` en Tcl…
- En mode live, `exit()`/`hiQuit()`/`pyKillServer()` sont toujours refusés (ils fermeraient la session de l'utilisateur).
- Les chemins passés aux outils doivent être sous `allowed_roots` ou `workdir`.
- Ces règles sont un **garde-fou contre les erreurs**, pas un bac à sable : quelqu'un qui écrit du SKILL peut
  tout faire. Gardez le niveau `run` sauf besoin explicite.

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `CADENCE_MCP_CONFIG` | Fichier JSON de configuration |
| `CADENCE_MCP_ACCESS` | `read` / `run` / `write` |
| `CADENCE_MCP_ALLOW_SHELL` | `1` pour autoriser les appels shell |
| `CADENCE_MCP_WORKDIR` | Répertoire des runs (défaut `~/cadence_mcp_runs`) |
| `CADENCE_MCP_ALLOWED_ROOTS` | Racines autorisées, séparées par `:` |
| `CADENCE_MCP_PROFILE` / `CADENCE_MCP_SETUP` | Profil par défaut / script de setup unique |
| `CADENCE_MCP_PROJECT_DIR` | Répertoire de départ du Virtuoso batch (contient `cds.lib`) |
| `CADENCE_MCP_SKILLBRIDGE_ID`, `CADENCE_MCP_SKILLBRIDGE_TCP` | Session skillbridge, sockets TCP |
| `CADENCE_MCP_NOCDSINIT` | `1` pour lancer le batch avec `-nocdsinit` |
| `CADENCE_MCP_BIN_<OUTIL>` | Chemin forcé d'un exécutable (ex. `CADENCE_MCP_BIN_SPECTRE`) |
| `CADENCE_MCP_TIMEOUT`, `CADENCE_MCP_MAX_OUTPUT` | Timeout par défaut (s), taille max des sorties |

## Runs longs et historique

Les outils de run acceptent `wait=false` : ils rendent un `run_id` tout de suite, puis `cadence_get_run(run_id)`
donne l'état, la fin du log et un résumé analysé à la fin. Si `wait=true` dépasse `timeout_s`, le run
continue en arrière-plan. Chaque run garde un `job.json` dans son répertoire : `cadence_list_runs` et
`cadence_get_run` retrouvent aussi les runs lancés lors de sessions précédentes.

## Points à valider sur votre site

Ces comportements dépendent de la configuration locale et n'ont pas pu être testés sans les outils réels :

- **Virtuoso batch** : `-replay` par défaut ; passez `"batch_flag": "-restore"` si votre wrapper `virtuoso` le préfère.
- **Allegro batch** : si `exit` demande une confirmation de sauvegarde dans votre release, ajoutez la réponse
  dans votre code ou passez les options produit via `extra_args`.
- **OrCAD Capture** : `capture.exe script.tcl` ouvre l'application ; le script doit fermer le design. Expérimental.
- **Lecture des résultats** : `cadence_psf_read` ne décode que le PSF ASCII (format par défaut de `cadence_spectre_run`).
- **Log CIW** : si `cadence_virtuoso_read_ciw_log` ne trouve pas le bon `CDS.log`, fixez `CADENCE_MCP_CDS_LOG`.

## Évaluations

Le dossier `evaluations/` contient 10 questions au format `mcp-builder`, un environnement de démonstration
figé et un script de vérification (voir `evaluations/README.md`).

## Tests

```bash
pip install ".[test]" && pytest
```

Les tests utilisent de faux exécutables Cadence (`tests/fakes.py`) et un faux espace skillbridge : ils
vérifient l'intégration (lignes de commande, détection de version, garde-fous, parseurs, serveur MCP en stdio)
sans licence Cadence. La syntaxe du fichier SKILL est vérifiée (parenthèses, chaînes, préfixes), pas son
exécution : faites un premier essai avec `cadence_virtuoso_get_info` puis `cadence_virtuoso_list_libraries` sur votre installation.
