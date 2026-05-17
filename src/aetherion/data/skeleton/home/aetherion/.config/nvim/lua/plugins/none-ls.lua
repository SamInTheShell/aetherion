return {
	"nvimtools/none-ls.nvim",
	dependencies = { "nvimtools/none-ls-extras.nvim" },
	config = function()
		local null_ls = require("null-ls")

		null_ls.setup({
			sources = {
				null_ls.builtins.formatting.stylua,
				null_ls.builtins.formatting.prettier,
				null_ls.builtins.formatting.black,
				null_ls.builtins.formatting.isort,
				null_ls.builtins.formatting.goimports,
				null_ls.builtins.formatting.terraform_fmt,
				null_ls.builtins.completion.spell,
				require("none-ls.diagnostics.eslint"), -- requires none-ls-extras.nvim
			},
		})

		vim.keymap.set("n", "<leader>gf", vim.lsp.buf.format, {})

		local exclude_filetypes = { "json", "jsonc" }

		-- Auto-format on save (excluding JSON files)
		vim.api.nvim_create_autocmd("BufWritePre", {
			callback = function()
				local filetype = vim.bo.filetype
				local should_format = true

				for _, ft in ipairs(exclude_filetypes) do
					if filetype == ft then
						should_format = false
						break
					end
				end

				if should_format then
					vim.lsp.buf.format({ async = false })
				end
			end,
		})
		-- vim.api.nvim_create_autocmd("BufWritePre", {
		-- 	callback = function()
		-- 		if vim.bo.filetype ~= "json" then
		-- 			vim.lsp.buf.format({ async = false })
		-- 		end
		-- 	end,
		-- })
	end,
}
