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
