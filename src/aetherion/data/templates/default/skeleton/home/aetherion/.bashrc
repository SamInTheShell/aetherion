# ~/.bashrc — aetherion dev container
#
# Sourced for interactive shells. /etc/skel/.profile (still in place from
# useradd) handles the login-shell -> .bashrc handoff, so a `bash -l` startup
# lands here too.

# Interactive guard — non-interactive shells bail out before doing any setup.
case $- in
    *i*) ;;
      *) return;;
esac

# ---- history -------------------------------------------------------------
HISTCONTROL=ignoreboth
HISTSIZE=10000
HISTFILESIZE=20000
shopt -s histappend
shopt -s checkwinsize

# ---- bash completion ----------------------------------------------------
if ! shopt -oq posix; then
    if [ -f /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    elif [ -f /etc/bash_completion ]; then
        . /etc/bash_completion
    fi
fi

# ---- readline: make Ctrl-W match Alt-B's word boundaries ----------------
# Two pieces are required, because two layers race for Ctrl-W:
#   1. termios WERASE (kernel-level, whitespace-only) — disabled here so
#      readline actually gets the keystroke.
#   2. readline's \C-w binding — rebound to backward-kill-word, whose word
#      definition (alphanumeric) matches backward-word / Alt-B.
# Without (1), the bind in (2) is silently ignored and Ctrl-W keeps eating
# URLs and paths whole.
stty werase undef 2>/dev/null
bind '"\C-w": backward-kill-word'

# ---- starship prompt ----------------------------------------------------
# Config lives at ~/.config/starship.toml (neon palette, shared across
# every baked-in template per STYLE.md).
eval "$(starship init bash)"
