return {
	"nvim-tree/nvim-tree.lua",
	version = "*",
	lazy = false,
	dependencies = {
		"nvim-tree/nvim-web-devicons",
	},
	config = function()
		-- disable netrw at the very start of your init.lua
		vim.g.loaded_netrw = 1
		vim.g.loaded_netrwPlugin = 1

		-- optionally enable 24-bit colour
		vim.opt.termguicolors = true

		require("nvim-tree").setup({
			sort = {
				sorter = "case_sensitive",
			},
			view = {
				width = 35,
			},
			renderer = {
				group_empty = true,
			},
			filters = {
				dotfiles = false,
				git_ignored = false,
				custom = {},
			},
			filesystem_watchers = {
				enable = true,
			},
			actions = {
				change_dir = {
					restrict_above_cwd = true,
				},
			},
		})

		-- Auto-close vim when only nvim-tree is open
		vim.api.nvim_create_autocmd("BufEnter", {
			group = vim.api.nvim_create_augroup("NvimTreeClose", { clear = true }),
			pattern = "NvimTree_*",
			callback = function()
				local layout = vim.api.nvim_call_function("winlayout", {})
				if layout[1] == "leaf" and vim.api.nvim_buf_get_option(vim.api.nvim_win_get_buf(layout[2]), "filetype") == "NvimTree" and layout[3] == nil then
					vim.cmd("confirm quit")
				end
			end
		})

		vim.keymap.set("n", "<C-b>", ":NvimTreeToggle<CR>")
	end,
}