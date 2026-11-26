#!/usr/bin/env bash
# Lanceur du serveur MCP cadence pour le plugin Claude Code.
#
#   cadence-mcp-launch.sh                 -> démarre le serveur MCP (stdio)
#   cadence-mcp-launch.sh --install-only  -> (ré)installe l'environnement Python puis s'arrête
#   cadence-mcp-launch.sh doctor|cdsinit  -> sous-commandes de cadence-mcp
#
# L'environnement Python est créé dans ${CLAUDE_PLUGIN_DATA} (conservé entre les mises à jour du
# plugin) et réinstallé automatiquement quand la version du plugin change.
# Tous les messages vont sur stderr : stdout est réservé au protocole MCP.
set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DATA="${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/cadence-mcp}"
VENV="$DATA/venv"
STAMP="$VENV/.cadence-plugin-stamp"

log() { printf '[cadence-mcp] %s\n' "$*" >&2; }

# Valeurs du plugin (userConfig) : ignorées si vides ou non substituées, pour ne pas
# écraser les variables CADENCE_MCP_* déjà définies dans le shell de l'utilisateur.
uc() { case "${1:-}" in ""|'${'*) return 1 ;; *) printf '%s' "$1" ;; esac; }
if v=$(uc "${CADENCE_MCP_UC_CONFIG:-}"); then export CADENCE_MCP_CONFIG="$v"; fi
if v=$(uc "${CADENCE_MCP_UC_ACCESS:-}"); then export CADENCE_MCP_ACCESS="$v"; fi
PY_WANTED=$(uc "${CADENCE_MCP_UC_PYTHON:-}" || uc "${CADENCE_MCP_PYTHON:-}" || true)
unset CADENCE_MCP_UC_CONFIG CADENCE_MCP_UC_ACCESS CADENCE_MCP_UC_PYTHON

find_python() {
  # Prints the absolute path of a Python >= 3.10 (works with python3.x, python, and the Windows "py" launcher).
  local c exe
  for c in ${PY_WANTED:+"$PY_WANTED"} python3.13 python3.12 python3.11 python3.10 python3 python py; do
    command -v "$c" >/dev/null 2>&1 || continue
    if [ "$c" = "py" ]; then
      exe=$(py -3 -c 'import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)' 2>/dev/null) || continue
    else
      exe=$("$c" -c 'import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)' 2>/dev/null) || continue
    fi
    [ -n "$exe" ] && { printf '%s' "$exe" | tr -d '\r'; return 0; }
  done
  return 1
}

venv_bin() {  # Windows venvs use Scripts/, POSIX venvs use bin/
  if [ -d "$VENV/Scripts" ]; then printf '%s' "$VENV/Scripts"; else printf '%s' "$VENV/bin"; fi
}

have_server() {
  local b; b=$(venv_bin)
  [ -x "$b/cadence-mcp" ] || [ -x "$b/cadence-mcp.exe" ]
}

stamp_value() {
  # Change à chaque nouvelle version du plugin (contenu du serveur embarqué).
  cat "$ROOT/.claude-plugin/plugin.json" "$ROOT/server/pyproject.toml" "$ROOT"/server/src/cadence_mcp/*.py \
      "$ROOT/server/src/cadence_mcp/skill/cadence_mcp.il" 2>/dev/null | cksum | cut -d' ' -f1
}

install_env() {
  local py
  if ! py=$(find_python); then
    log "Python >= 3.10 introuvable. Installez-le (ex. 'dnf install python3.11') ou indiquez son chemin"
    log "dans la configuration du plugin (champ « Python >= 3.10 ») ou via CADENCE_MCP_PYTHON."
    exit 1
  fi
  log "installation de l'environnement dans $VENV (Python: $py)"
  mkdir -p "$DATA"
  rm -rf "$VENV"
  "$py" -m venv "$VENV" >&2
  "$(venv_bin)/python" -m pip install --quiet --upgrade pip >&2 || true
  "$(venv_bin)/python" -m pip install --quiet "$ROOT/server[virtuoso]" >&2
  stamp_value > "$STAMP"
  log "environnement prêt"
}

if ! have_server || [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$(stamp_value)" ]; then
  install_env
fi

if [ "${1:-}" = "--install-only" ]; then
  exit 0
fi
exec "$(venv_bin)/cadence-mcp" "$@"
