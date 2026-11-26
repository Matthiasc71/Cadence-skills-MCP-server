# Plugin Claude Code « cadence »

Ce plugin relie Claude Code aux outils Cadence Design Systems : Virtuoso/SKILL/OCEAN, Spectre (APS, Spectre X), Genus, Innovus, Tempus, Voltus, Xcelium et Allegro/OrCAD. Il couvre Virtuoso IC6.1.7 à IC25.1 et les releases associées.

Il contient trois éléments :

- le serveur MCP `cadence` (dans `server/`, Python ≥ 3.10) ;
- le skill `cadence-eda`, qui donne à Claude les règles propres à chaque version ;
- les commandes `/cadence:setup`, `/cadence:doctor` et `/cadence:cdsinit`.

Installation : `claude plugin marketplace add Matthiasc71/Cadence-skills-MCP-server` puis `claude plugin install cadence@cadence-eda`.

Après l'installation, lancez `/cadence:setup`, puis `/mcp` pour reconnecter le serveur. La configuration multi-versions (profils) est décrite dans `server/README.md`.
