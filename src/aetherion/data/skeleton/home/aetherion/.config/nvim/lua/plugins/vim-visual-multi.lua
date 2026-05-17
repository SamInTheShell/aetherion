return {
	"brenton-leighton/multiple-cursors.nvim",
	version = "*",
	opts = {},
	keys = {
		{
			"<M-LeftMouse>",
			"<Cmd>MultipleCursorsMouseAddDelete<CR>",
			mode = { "n", "i" },
			desc = "Add or remove cursor with Alt+Click",
		},
		{ "<M-j>", "<Cmd>MultipleCursorsAddDown<CR>", mode = { "n", "i" }, desc = "Add cursor below" },
		{ "<M-k>", "<Cmd>MultipleCursorsAddUp<CR>", mode = { "n", "i" }, desc = "Add cursor above" },
		{ "<M-Down>", "<Cmd>MultipleCursorsAddDown<CR>", mode = { "n", "i" }, desc = "Add cursor below" },
		{ "<M-Up>", "<Cmd>MultipleCursorsAddUp<CR>", mode = { "n", "i" }, desc = "Add cursor above" },
		{ "<C-j>", "<Cmd>MultipleCursorsAddDown<CR>", mode = { "n", "i" }, desc = "Add cursor below" },
		{ "<C-k>", "<Cmd>MultipleCursorsAddUp<CR>", mode = { "n", "i" }, desc = "Add cursor above" },
		{ "<C-n>", "<Cmd>MultipleCursorsAddMatches<CR>", mode = { "n", "v" }, desc = "Add cursors to matches" },
	},
}
