# NeoVim Config

This is just my nvim configuration.

## Installing Nerd Fonts and NeoVim

Nerd fonts are necessary for some of the plugins installed.

This is a very quick and lazy way to install them all.

```
brew install neovim
brew search '/font-.*-nerd-font/' | awk '{ print $1 }' | xargs -I{} brew install --cask {} || true
```

## Terminal Setup

On MacOS, use [iTerm2](https://iterm2.com/downloads.html).

A default profile has been provided [here](https://raw.githubusercontent.com/samintheshell/nvim/refs/heads/main/iterm2.profile.json).

Open `iTerm2`

Go to `iTerm2 > Settings... > Profiles > Other Actions... > Import JSON Profiles...` and import `iterm2.profile.json`.

Set the new profile as the default.

Go to `iTerm2 > Settings... > Appearance > Theme` select `Minimal`.

## Using this Config

Clone the config.

```
mkdir -p ~/.config && cd $_
git clone git@github.com:samintheshell/nvim.git
```

Setup `:Mason` packages for the LSP configuration.

```
nvim --headless \
    -c 'MasonInstall stylua' \
    -c 'MasonInstall prettier' \
    -c 'MasonInstall black' \
    -c 'MasonInstall isort' \
    -c 'MasonInstall goimports' \
    -c 'qa'
```

Open `nvim` in the terminal.

## Basic Editing

In normal mode, use `j` and `k` to move cursor up or down.

In normal mode, use `h` and `l` to move cursor left or right.

In normal mode, use `w` and `b` to move cursor left or right jumping over words.

In normal mode, use `H`, `M`, and `L` to move the cursor to the first, middle, and last lines in view.

In normal mode, use `<line-number>G` to jump directly to the specified line number.

In normal mode, use `Ctrl+d` and `Ctrl+u` to scroll half page.

In normal mode, use `v` to select text.

In normal mode, use `V` to select lines.

In visual mode, use `y` to copy selected text.

In visual mode, use `c` to cut selected text.

In normal mode, use `P` to paste copied text before.

In normal mode, use `p` to paste copied text after.

In normal mode, use `I` to insert at start of line.

In normal mode, use `gI` to insert at start of line, ignoring indentation.

In normal mode, use `A` to insert at end of line.

In normal mode, use `gg` to jump to first line of file.

In normal mode, use `G` to jump to end line of file.

In normal mode, use `o` to start a new line below the current line.

In normal mode, use `O` to start a new line above the current line.

In normal mode, use `J` to join the current line with the next one.

In normal mode, use `u` to undo last change.

In normal mode, use `Ctrl + r` to redo last change.

In normal mode, use `>` to indent text.

In normal mode, use `<` to dedent text.

The `<leader>` key is mapped to spacebar, this is important to know when viewing key bindings.

Press these keys in order to open the full keymap: `<leader>fk`

## Terminals

In normal mode, use `:terminal` will replace the current window with a terminal session.

In normal mode, press `i` to enter insert mode to use the terminal.

Press `Ctrl + \` followed by `Ctrl + n` to get back to normal mode.

In normal mode, use `:w somefilename.txt` to save the buffered terminal to a file.

## Clearing Popup Windows

Sometimes things like Mason or Lazy or Telescope windows are in the way.

They can typically be cleared by just spamming `Escape` or `Ctrl + c`.

## Multiline Editing

Move the cursor to where you want the cursor to be lined up.

In normal mode, use `Ctrl + v` to enable vertical block selection.

Press `I` to go into insert mode.

Make your changes. They will only show on the first line.

Escape insert mode and the changes will be applied to all the selected lines.

## Buffers

When you quit a file with `:q`, it will still be in a buffer in the background.

In normal mode, use `<leader>fb` to open the Telescope buffer list.

Select a buffer and press enter to open it.

With the buffer opened, use `:bd` to delete the buffer (or `:bd!` when unsaved changes are in the buffer).

To close all buffers, use `:%bd` (or `:%bd!` to ignore unsaved changes).

## Windows

Just see `:help :wincmd` for more info.

In normal mode, `<leader>n<arrow>` creates a new window beside the active one based on the `<arrow>` direction.

In normal mode, `<leader>nn` creates a new window below the active window.

In normal mode, `Ctrl+w w` moves to the next window.

In normal mode, `Ctrl+w W` moves to the previous window.

In normal mode, `Ctrl+w <` will decrease the current window width.

In normal mode, `Ctrl+w >` will increase the current window width.

In normal mode, `Ctrl+w -` will decrease the current window height.

In normal mode, `Ctrl+w +` will increase the current window height.

In normal mode, `<leader>rh` is used for resizing the window width. You must type in a number.

In normal mode, `<leader>rv` is used for resizing the window height. You must type in a number.

In normal mode, `Ctrl+w T` moves the active window to a new tab. Use `gt` and `gT` to cycle tabs.

## Telescope

Using `Ctrl+/` will show available key commands when a Telescope window is active.

### Buffers

In normal mode, `<leader>fb` opens `:Telescope buffers`.

Selecting a buffer and pressing `<enter>` will switch the active window to that buffer.

Selecting a buffer and pressing `Ctrl+x` will open the buffer in a new window below the active window.

## Misc

In normal or visual mode, `<leader>tm` toggles the mouse support off/on.

## Diagnostic Signs

LSP diagnostics are displayed in the sign column (gutter) with single letter indicators:

- `E` - Errors (red) - Code that will not compile or has critical issues
- `W` - Warnings (yellow) - Code that works but has potential issues or style violations
- `I` - Information (blue) - General information about the code
- `H` - Hints (light) - Suggestions for code improvements

These signs appear in the right column of the gutter, with git status indicators (+ ~ \_) appearing in the left column.

Use `<space>e` to open a floating window with detailed diagnostic information for the current line.

Use `]d` and `[d` to jump to the next and previous diagnostic respectively.

## Multi-Cursor Editing

In normal mode, use `Ctrl+n` to select the word under cursor and add next match.

In normal mode, use `Ctrl+p` to select the word under cursor and add previous match.

In normal mode, use `Ctrl+Down` and `Ctrl+Up` to add cursors vertically.

In normal mode, use `\\A` to select all occurrences of the word under cursor.

In multi-cursor mode, use `c` to change all selected words.

In multi-cursor mode, use `d` to delete all selected words.
