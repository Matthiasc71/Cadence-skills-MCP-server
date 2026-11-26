---
tags: [skill-creator]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

On migre de Virtuoso IC6.1.8 vers IC23.1 le mois prochain. Notre script de régression nocturne (run_regression.il, en pièce jointe) lance les simulations de amp_lib/tb_ota via l'API ADE XL. Adapte-le pour qu'il marche sur IC23.1, et si possible encore sur IC6.1.8 pendant la transition. Mets le nouveau script dans run_regression_ic231.il et explique ce que tu as changé et ce qu'il faut vérifier dans NOTES.md.

Input files: run_regression.il