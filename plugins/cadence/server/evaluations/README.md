# Évaluations du serveur MCP (méthode mcp-builder)

`cadence_eval.xml` contient 10 questions réalistes, indépendantes, en lecture seule, avec une réponse
unique vérifiable par comparaison de chaînes. Elles portent sur un **environnement de démonstration figé**
(aucune licence Cadence nécessaire) que construit `build_demo_env.py` :

- 3 profils de versions : `ic617` (IC6.1.7 + SPECTRE 18.1), `ic231` (IC23.1 + 23.1), `ic251` (IC25.1 + 25.1) ;
- bibliothèques Virtuoso `analogLib` (64 cellules, donc 2 pages), `amp_lib`, `digital_io` ;
- runs archivés : Spectre AC / transitoire / en échec, Genus, Innovus (preCTS, postCTS, postRoute), deux régressions xrun.

## Vérifier les réponses (sans clé API)

```bash
python evaluations/build_demo_env.py /tmp/cadence-demo
python evaluations/verify_answers.py /tmp/cadence-demo/config.json
```

Le script résout chaque question avec des appels MCP en lecture seule. Il tourne aussi dans `pytest`
(`tests/test_evaluations.py`).

## Lancer l'évaluation par un LLM (banc de mcp-builder)

Le banc `scripts/evaluation.py` du skill `mcp-builder` (dépôt `anthropics/skills`) donne les questions à
Claude, qui n'a que les outils du serveur, puis note les réponses :

```bash
git clone --depth 1 https://github.com/anthropics/skills /tmp/skills
pip install -r /tmp/skills/skills/mcp-builder/scripts/requirements.txt
export ANTHROPIC_API_KEY=...
python evaluations/build_demo_env.py /tmp/cadence-demo
python /tmp/skills/skills/mcp-builder/scripts/evaluation.py evaluations/cadence_eval.xml \
  -t stdio -c cadence-mcp -a serve -e CADENCE_MCP_CONFIG=/tmp/cadence-demo/config.json \
  -o evaluation_report.md
```

Le rapport donne la précision, le nombre d'appels d'outils par question et les retours de l'agent sur
les outils : c'est la source principale pour améliorer descriptions et formats de réponse.

## Évaluer sur votre vraie installation

Écrivez un second fichier sur le même modèle, à partir de données **figées** de vos projets : runs
terminés, bibliothèques de référence, rapports de tape-out. N'utilisez pas de données qui bougent
(runs en cours, cellules en cours d'édition). Lancez ensuite le même banc avec votre `config.json`.
