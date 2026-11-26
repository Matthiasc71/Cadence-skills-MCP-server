---
name: cadence-eda
description: Expertise multi-versions pour les outils Cadence Design Systems (Virtuoso IC6.1.7 à IC25.1, SKILL/SKILL++, OCEAN, ADE L/XL/Explorer/Assembler, Spectre/APS/Spectre X, Genus, Innovus, Tempus, Voltus, Xcelium, Allegro, OrCAD) et pilotage via le serveur MCP cadence. À utiliser dès qu'une demande touche un de ces outils, un script SKILL, OCEAN ou Tcl Cadence, une migration de version (IC6.1.x vers IC23.1/IC25.1, legacy vers Stylus, irun vers xrun), une simulation, un log ou un rapport Cadence, même si l'utilisateur ne parle ni de MCP ni de version.
---

# Cadence EDA : bonnes pratiques multi-versions

Les outils Cadence changent beaucoup d'une release à l'autre (API ADE, dialectes Tcl, moteurs Spectre).
Le code qui marche sur IC6.1.8 peut échouer sur IC23.1. Ce skill donne les règles qui évitent ces pièges,
et la façon de travailler avec le serveur MCP `cadence` quand il est branché.

## 1. Savoir dans quel environnement on travaille

Avec le serveur MCP `cadence` (outils `cadence_*`) :

1. `cadence_get_environment` : outils installés, versions, profils, niveau d'accès. S'il y a plusieurs
   profils (ex. `ic617`, `ic231`, `ic251`) et que la demande ne dit pas lequel, demande-le ; passe ensuite
   `profile=` à chaque appel.
2. Custom IC : `cadence_virtuoso_get_info`. Regarde `mode` (`live` = la session de l'utilisateur via
   skillbridge, `batch` = un `virtuoso -nograph` séparé), `parsed_version`, `features` et `hints`.
3. Choisis le code d'après les **sondes d'exécution** (`features`, `isCallable('fn)`, `is_common_ui_mode`)
   plutôt que d'après le seul numéro de version : un site peut avoir des ISR ou des produits différents.

Dans Claude Code avec le plugin `cadence`, si le serveur ne répond pas, propose `/cadence:setup` (installation),
`/cadence:doctor` (diagnostic) ou `/cadence:cdsinit` (connexion de Virtuoso).

Sans le serveur MCP : écris quand même le code demandé, en fichiers prêts à l'emploi. Dis clairement qu'il
n'a pas été exécuté et indique comment le tester (une commande, ou une vérification dans le CIW).

## 2. Ce qui change d'une version à l'autre

| Sujet | IC6.1.7 / IC6.1.8 | ICADVM18.1 / 20.1 | IC23.1 / IC25.1 (Virtuoso Studio) |
|---|---|---|---|
| Environnement de simulation | ADE L/XL (dépréciés) + ADE Explorer/Assembler | Explorer/Assembler (pas d'ADE L) | **Uniquement** maestro : Explorer/Assembler (+ ADE Artist en IC25.1) |
| Vues | `adexl`, états ADE L, `maestro` | `maestro` | `maestro` (les `adexl` doivent être converties) |
| API SKILL | `sev*` (ADE L), sessions `axl*`, `mae*` | sessions `axl*`, `mae*` | `mae*` et sessions `axl*` (qui pilotent maestro) |
| skillbridge (mode live) | à partir d'IC6.1.7 | oui | oui |
| Divers | – | – | IC25.1 : plus de `.simrc` ; menu Parasitics du schéma déplacé dans ADE |

- Avant IC6.1.7 (et IC5/CDB) : pas de skillbridge, batch seulement ; hors du périmètre testé.
- **Piège principal** : IC23.1 a supprimé les *environnements* ADE L et ADE XL, pas l'API `axl*`. Les
  fonctions de session `axlCreateSession`, `axlGetWindowSession`, `axlSetMainSetupDBLCV`… existent
  toujours et servent à piloter les vues maestro. `isCallable('axlCreateSession)` est donc vrai sur
  toutes les releases et ne permet pas de les distinguer. Pour choisir une branche, teste la release
  (`getVersion(t)`, ex. `"sub-version  IC23.1-64b.ISR9.24"`) et/ou la vue présente
  (`ddGetObj(lib cell "maestro")`).
- **Stratégie de migration la plus sûre** : convertir une fois la vue `adexl` en vue `maestro` (ADE Assembler
  sait l'importer ; un état ADE L se charge dans ADE Explorer par Session > Load State), puis piloter la vue
  maestro avec les fonctions `mae*` sur IC6.1.8 comme sur IC23.1, puisque maestro existe depuis IC6.1.7
  (confirme `isCallable('maeOpenSetup)` sur l'ISR du site). Ne garde une branche `axl*`/adexl pour IC6.1.8
  que si la conversion n'est pas encore faite, et sélectionne-la par la release et la vue, jamais par
  `isCallable` d'une fonction `axl*`.
- Les noms et arguments des fonctions `mae*` varient selon l'ISR : vérifie-les dans la doc de la release
  (`cdnshelp`, « Virtuoso ADE SKILL Reference ») plutôt que de les inventer, et marque ceux que tu n'as pas
  pu confirmer.
- En batch (`virtuoso -nograph`), un script de régression doit **attendre la fin des simulations** avant de
  lire les résultats ou de quitter : option d'attente de `maeRunSimulation` ou fonction d'attente selon
  l'ISR (à vérifier). Sinon le processus se termine pendant que les jobs tournent encore.
- Relis le script d'origine et signale ses défauts (variable `t`, valeur de retour ignorée, argument
  invalide) : ils casseraient aussi la version migrée si tu les recopiais.
- **OCEAN** (`simulator`, `design`, `analysis`, `desVar`, `run`, `selectResult`, `ocnPrint`, `value`)
  fonctionne sur toutes les versions : c'est le choix le plus portable pour scripter une simulation.

**Spectre** : APS existe partout : `+aps` ou `++aps` (plus rapide), avec un errpreset : `++aps=liberal`
est le réglage le plus rapide, `conservative` le plus précis. Sur la ligne de commande, cet errpreset
remplace celui de l'analyse `tran` du netlist, ce qui peut dégrader la précision de transitoires sensibles.
Spectre X (`+preset=cx|ax|mx|lx|vx`, du plus précis au plus rapide) n'existe qu'à partir de SPECTRE 19.1 et
ignore `+aps`. Sur une release plus ancienne, prends le réglage APS demandé (`++aps=liberal` pour « le plus
rapide ») avec `+mt=N`, explique pourquoi et donne la ligne à changer après la mise à jour (`+preset=vx`). Un
script peut aussi lire la version (`spectre -W`) et choisir seul. `cadence_spectre_run` fait ce repli ;
signale-le. Spectre FX (`spectrefx`) : transitoire seulement, mode SPICE par défaut. Pour des résultats
lisibles en texte : `-format psfascii -raw <dossier>`.

**Numérique** : Genus démarre en Stylus Common UI (`-legacy_ui` pour l'ancien mode) ; Innovus, Tempus et
Voltus démarrent en legacy (`-stylus` pour le Common UI). `cadence_digital_run_tcl(ui="auto")` détecte le
dialecte du script ; force `ui=` s'il mélange les deux. Pour traduire, exécute dans l'outil
`get_common_ui_map <commande_legacy>` ; pour un script entier `convert_legacy_to_common_ui in.tcl out.tcl` ;
`eval_legacy {...}` en dernier recours.

| Legacy (Innovus) | Stylus Common UI |
|---|---|
| `setMultiCpuUsage -localCpu 8` | `set_multi_cpu_usage -local_cpu 8` |
| `setDesignMode -process 28` | `set_db design_process_node 28` |
| `placeDesign` / `routeDesign` | `place_design` / `route_design` |
| `timeDesign -preCTS` / `-postCTS` | `time_design -pre_cts` / `-post_cts` |
| `optDesign -postCTS` | `opt_design -post_cts` |
| `saveDesign` / `restoreDesign` | `write_db` / `read_db` |
| `dbGet` | `get_db` |
| Genus legacy `set_attribute a v /` | `set_db a v` |

Pour une correspondance absente de ce tableau, écris quand même l'équivalent Stylus le plus probable et
marque-le : les options des commandes `setXxxMode -opt valeur` deviennent en général des attributs
`set_db opt valeur` (ex. `setPlaceMode -place_global_timing_effort high` → `set_db place_global_timing_effort high`),
et les options en camelCase passent en snake_case (`-preCTS` → `-pre_cts`, `-outDir` → `-report_dir`). Ajoute
sur la ligne ou juste au-dessus `# A VERIFIER: get_common_ui_map <cmd legacy>` et liste ces points dans ta
réponse. N'utilise `eval_legacy {...}` qu'en dernier recours, quand aucun équivalent n'est plausible : il
laisse une commande legacy dans un script Stylus, ce qui contredit le but de la conversion. Si la base d'entrée a été écrite en legacy (`saveDesign`, `.enc.dat`),
signale que sa lecture par `read_db` est à confirmer, ou propose de migrer aussi l'étape précédente.

Lancement en batch : `innovus -stylus -no_gui -files script.tcl -log logs/run` (finis le script par `exit`,
ferme l'entrée standard avec `< /dev/null` sous `nohup`/LSF). `-no_gui` est l'option documentée ; pour une
autre option, renvoie à `innovus -help` de la release plutôt que de l'affirmer.

**Xcelium** : `xrun` en une étape (compile + élabore + simule) ; `irun`/Incisive sur les vieilles
installations ; les binaires `nc*` sont devenus `xm*`.

**PCB** : Allegro se scripte en SKILL `axl*` (batch : `allegro -s script.scr -nograph carte.brd`, fait par
`cadence_allegro_run_skill`) ; OrCAD (X) Capture en Tcl (`cadence_capture_run_tcl`, expérimental). À partir
de 22.1, gamme Allegro X / OrCAD X.

## 3. Écrire du SKILL portable

- `.il` = SKILL (portée dynamique), `.ils` = SKILL++ (portée lexicale) : ne mélange pas les idiomes.
- Préfixe procédures et variables globales (ex. `MyProjFoo`) : tout vit dans le même espace de noms que
  Cadence et le PDK.
- N'utilise jamais `t` ou `nil` comme nom de variable : `foreach(t tests ...)` masque la constante « vrai »
  et casse le code qui l'utilise ensuite.
- Ouvre en lecture `dbOpenCellViewByType(lib cell view nil "r")` et ferme avec `dbClose(cv)` même en cas
  d'erreur (`errset` ou `unwindProtect`), sinon le cellview reste verrouillé ou chargé.
- Protège tout ce qui est récent ou propre à ICADVM par `isCallable`.
- Préfère `foreach`/`setof`/`exists` et `~>` aux boucles manuelles ; `sprintf(nil "%L" x)` pour afficher un objet.
- En mode live, évite les formulaires modaux et les boucles longues : la session de l'utilisateur
  serait bloquée et le pont désynchronisé (il faudra `pyReloadScript()` puis `pyStartServer()`).
- Passe le fichier par `cadence_skill_lint` (sklint) avant de le livrer quand c'est possible.

## 4. Déroulé type avec le serveur MCP

- **Inspecter** : `cadence_virtuoso_list_libraries` → `cadence_virtuoso_list_cells(library, pattern)` →
  `cadence_virtuoso_get_cellview(library, cell, view)`. En batch, `cwd` (ou `project_dir`) doit contenir `cds.lib`.
- **Simuler** : `cadence_virtuoso_create_netlist` (ou un netlist fourni) → `cadence_spectre_run(..., wait=false)`
  → `cadence_get_run(run_id)` jusqu'à la fin → `cadence_psf_read(path, signals=[...])`. Pour l'AC,
  `cadence_psf_read` donne le gain en dB, la fréquence à -3 dB, la fréquence de gain unité et la phase à
  cette fréquence.
- **Numérique** : `cadence_digital_run_tcl(tool_name, script, ui, cpus, wait=false)` → `cadence_get_run` →
  `cadence_digital_parse_report` (WNS, TNS, chemins en violation, surface).
- **Vérification** : `cadence_xcelium_run(sources, top, options, uvm)` → `cadence_get_run`.
- **Historique** : `cadence_list_runs(tool_name=..., state=...)` puis `cadence_get_run` ; `cadence_find_files`
  pour retrouver logs et rapports.
- Les listes sont paginées : suis `next_offset` tant que `has_more` est vrai avant de conclure « il n'y en a
  pas d'autre ». Passe `response_format="json"` quand il te faut des valeurs exactes.

Pour tout run de plus de quelques minutes, utilise `wait=false` et donne le `run_id` à l'utilisateur.

## 5. Garde-fous et niveaux d'accès

Le serveur a trois niveaux : `read`, `run` (défaut), `write`. Les modifications de la base (`dbSave`,
`dbCreate*`, `dbDelete*`, `schCheck`, `obj~>attr = val`…) exigent `write` ; les appels shell (`system`,
`sh`, `ipcBeginProcess`, Tcl `exec`) exigent `allow_shell`. En live, `exit()` est toujours refusé.

Quand un appel est refusé : explique la raison et la variable à changer (`CADENCE_MCP_ACCESS=write`,
`CADENCE_MCP_ALLOW_SHELL=1`). Ne contourne pas le refus avec `evalstring`, `funcall`, `load` ou un fichier
intermédiaire : le refus protège les bibliothèques de l'utilisateur. Avant toute écriture dans une
bibliothèque, décris ce qui sera modifié et attends son accord explicite.

## 6. Dépannage rapide

| Symptôme | Piste |
|---|---|
| `cadence_virtuoso_connect` : « No server found » | Dans le CIW : `load("…/python_server.il")` puis `pyStartServer()` (lignes exactes : `cadence-mcp cdsinit`) |
| Batch : « exited without writing a result » | Licence, `cds.lib` absent du `cwd`, ou wrapper `virtuoso` du site : voir `log_tail`, essayer `batch_flag "-restore"` |
| Timeout en live | Formulaire ouvert ou boucle ; redémarrer le pont, puis `cadence_virtuoso_connect` |
| `cadence_psf_read` refuse le fichier | Résultats binaires : relancer avec `output_format="psfascii"` |
| Innovus : « command not found » | Mauvais dialecte : forcer `ui="stylus"` ou `ui="legacy"` |
| Allegro sans fichier résultat | Sélection de produit ou licence : voir `journals` et `extra_args` |

Dans le compte rendu, cite la version et le profil utilisés, et dis si l'exécution a eu lieu dans la
session live de l'utilisateur, dans un batch séparé, ou pas du tout.
