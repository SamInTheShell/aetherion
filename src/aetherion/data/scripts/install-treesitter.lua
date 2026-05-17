-- Build-time treesitter parser install helper.
--
-- :TSInstallSync prompts "X parser already available: would you like to
-- reinstall ? y/n:" when invoked on an already-present parser, which in
-- headless mode loops forever (no tty to answer). We dodge that by deleting
-- any stale .so files (from a previous build's interrupted async install)
-- and then asking nvim-treesitter to install everything from a clean slate
-- in one synchronous batch.
--
-- Parser list mirrors ensure_installed in
-- skeleton/.../plugins/nvim-treesitter.lua. Keep them in sync.

local want = {
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
}

local parser_dir = vim.fn.stdpath("data") .. "/lazy/nvim-treesitter/parser"
for _, p in ipairs(want) do
	os.remove(parser_dir .. "/" .. p .. ".so")
end

print("install-treesitter: installing " .. #want .. " parsers")
vim.cmd("TSInstallSync " .. table.concat(want, " "))
vim.cmd("qa")
