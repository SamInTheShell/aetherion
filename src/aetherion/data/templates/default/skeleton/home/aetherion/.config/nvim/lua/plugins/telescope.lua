return {
	"nvim-telescope/telescope.nvim",
	tag = "0.1.8",
	dependencies = {
		"nvim-lua/plenary.nvim",
	},
	config = function()
		local builtin = require("telescope.builtin")
		local actions = require("telescope.actions")
		local action_state = require("telescope.actions.state")

		-- Setup telescope with custom buffer mappings
		require("telescope").setup({
			pickers = {
				buffers = {
					mappings = {
						i = {
							["<C-x>"] = function(prompt_bufnr)
								local current_picker = action_state.get_current_picker(prompt_bufnr)
								current_picker:delete_selection(function(selection)
									local bufnr = selection.bufnr
									-- Find windows displaying this buffer
									local winids = vim.fn.win_findbuf(bufnr)
									-- Replace buffer in each window with a new empty buffer to keep windows open
									for _, winid in ipairs(winids) do
										local new_buf = vim.api.nvim_create_buf(false, true)
										vim.api.nvim_win_set_buf(winid, new_buf)
									end
									-- Delete the buffer
									vim.api.nvim_buf_delete(bufnr, { force = true })
								end)
							end,
						},
						n = {
							["<C-x>"] = function(prompt_bufnr)
								local current_picker = action_state.get_current_picker(prompt_bufnr)
								current_picker:delete_selection(function(selection)
									local bufnr = selection.bufnr
									-- Find windows displaying this buffer
									local winids = vim.fn.win_findbuf(bufnr)
									-- Replace buffer in each window with a new empty buffer to keep windows open
									for _, winid in ipairs(winids) do
										local new_buf = vim.api.nvim_create_buf(false, true)
										vim.api.nvim_win_set_buf(winid, new_buf)
									end
									-- Delete the buffer
									vim.api.nvim_buf_delete(bufnr, { force = true })
								end)
							end,
						},
					},
				},
			},
		})

		-- Keymaps
		vim.keymap.set("n", "<leader>ff", builtin.find_files, { desc = "Telescope find files" })
		vim.keymap.set("n", "<leader>fg", builtin.live_grep, { desc = "Telescope live grep" })
		vim.keymap.set("n", "<leader>fb", builtin.buffers, { desc = "Telescope buffers" })
		vim.keymap.set("n", "<leader>fh", builtin.help_tags, { desc = "Telescope help tags" })
		vim.keymap.set("n", "<leader>fc", builtin.commands, { desc = "Telescope commands" })
		vim.keymap.set("n", "<leader>fk", builtin.keymaps, { desc = "Telescope keymaps" })
		vim.keymap.set("n", "<leader>fm", builtin.marks, { desc = "Telescope marks" })
		vim.keymap.set("n", "<leader>fo", builtin.oldfiles, { desc = "Telescope oldfiles (recent files)" })
		vim.keymap.set("n", "<leader>cm", "<cmd>Telescope git_commits<CR>", { desc = "telescope git commits" })
		vim.keymap.set("n", "<leader>gt", "<cmd>Telescope git_status<CR>", { desc = "telescope git status" })

		-- Add custom grep subcommand after telescope loads
		vim.defer_fn(function()
			-- Delete existing telescope command
			vim.cmd("delcommand Telescope")

			-- Recreate with our custom logic
			vim.api.nvim_create_user_command("Telescope", function(opts)
				local subcommand = opts.fargs[1]

				if subcommand == "grep" then
					local search_term = table.concat(opts.fargs, " ", 2)
					if search_term == "" then
						builtin.grep_string({ search = vim.fn.input("Grep > ") })
					else
						builtin.grep_string({ search = search_term })
					end
				else
					-- Use telescope's internal command handling
					require("telescope.command").load_command(unpack(opts.fargs))
				end
			end, {
				nargs = "*",
				complete = function(ArgLead, CmdLine, CursorPos)
					local completions = {}

					-- Get from telescope's builtin functions
					for name, _ in pairs(builtin) do
						if name:match("^" .. ArgLead) then
							table.insert(completions, name)
						end
					end

					-- Add our custom grep command
					if string.match("grep", "^" .. ArgLead) then
						table.insert(completions, "grep")
					end

					return completions
				end,
			})
		end, 100)
	end,
}
