return {
	"folke/which-key.nvim",
	event = "VeryLazy",
	config = function()
		local wk = require("which-key")
		
		-- Store the original delay
		local enabled_delay = 500
		local disabled_delay = 999999
		local current_delay = enabled_delay
		
		wk.setup({
			preset = "helix",
			delay = function() return current_delay end,
			win = {
				border = "rounded",
				padding = { 1, 2 },
				wo = {
					winblend = 0,
				},
			},
		})

		-- Toggle function
		local function toggle_which_key()
			if current_delay == enabled_delay then
				current_delay = disabled_delay
				print("which-key disabled")
			else
				current_delay = enabled_delay
				print("which-key enabled")
			end
		end

		-- Toggle keybinding
		vim.keymap.set("n", "<leader>tw", toggle_which_key, { desc = "Toggle which-key" })

		-- Add groups
		wk.add({
			{ "<leader>f", group = "Find/File" },
			{ "<leader>g", group = "Git" },
			{ "<leader>l", group = "LSP" },
			{ "<leader>d", group = "Debug" },
			{ "<leader>t", group = "Terminal/Toggle" },
			{ "<leader>w", group = "Window" },
			{ "<leader>b", group = "Buffer" },
		})
	end,
}