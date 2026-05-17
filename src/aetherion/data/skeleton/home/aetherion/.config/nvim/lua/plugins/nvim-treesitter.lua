return {
	"nvim-treesitter/nvim-treesitter",
	branch = "master",
	lazy = false,
	build = ":TSUpdate",
	config = function()
		require("nvim-treesitter.configs").setup({
			ensure_installed = {
				"gdscript",
				"godot_resource",
				"gdshader",
				"bash",
				"lua",
				"javascript",
				"go",
				"typescript",
				"python",
				"rust",
				"markdown",
				-- markdown_inline is required alongside markdown: markdown
				-- parses block structure and injects markdown_inline for
				-- inline content (links, emphasis, code spans). Missing it
				-- crashes the highlighter on `:range()`.
				"markdown_inline",
				"vimdoc",
			},
			highlight = { enable = true },
			indent = {
				enable = true,
				-- Disable Treesitter indentation for Go because it interferes with
				-- Vim's built-in comment block indentation (/* */) behavior
				-- Disable for gdscript to use vim-godot's better indentation
				disable = { "go", "gdscript" },
			},
		})
	end,
}
