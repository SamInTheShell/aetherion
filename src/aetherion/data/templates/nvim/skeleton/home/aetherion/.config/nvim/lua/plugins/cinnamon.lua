return {
	"declancm/cinnamon.nvim",
	version = "*",
	config = function()
		require("cinnamon").setup({
			-- Default delay is 5ms, adjust if you want faster/slower
			delay = 4,
			-- Recommended settings for smooth scrolling
			max_delta = {
				time = 500, -- Maximum animation time (ms)
			},
		})
	end,
}
