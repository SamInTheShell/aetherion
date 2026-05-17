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

# ---- add bun bin to path ------------------------------------------------
export PATH="${PATH}:/home/aetherion/.bun/bin"

# ---- convenience aliases ------------------------------------------------
alias cursor='cursor-agent' # Doubtful this container will have Cursor IDE

# ---- starship prompt ----------------------------------------------------
# Config lives at ~/.config/starship.toml (catppuccin_mocha, see that file).
eval "$(starship init bash)"
