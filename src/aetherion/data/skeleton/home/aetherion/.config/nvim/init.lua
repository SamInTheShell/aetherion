-- Bootstrap lazy.nvim
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
	local lazyrepo = "https://github.com/folke/lazy.nvim.git"
	local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=stable", lazyrepo, lazypath })
	if vim.v.shell_error ~= 0 then
		vim.api.nvim_echo({
			{ "Failed to clone lazy.nvim:\n", "ErrorMsg" },
			{ out, "WarningMsg" },
			{ "\nPress any key to exit..." },
		}, true, {})
		vim.fn.getchar()
		os.exit(1)
	end
end
vim.opt.rtp:prepend(lazypath)

-- Make sure to setup `mapleader` and `maplocalleader` before
-- loading lazy.nvim so that mappings are correct.
-- This is also a good place to setup other settings (vim.opt)
vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

-- Set /usr/bin first so that default python gets used
-- vim.env.PATH = "/usr/bin:" .. vim.env.PATH

-- Godot project auto-listen setup
local function setup_godot_listen()
	local cwd = vim.fn.getcwd()
	local project_file = cwd .. "/project.godot"
	local socket_file = cwd .. "/godothost"

	-- Check if this is a Godot project
	if vim.fn.filereadable(project_file) == 1 then
		-- Start the server
		vim.fn.serverstart(socket_file)
	end
end

setup_godot_listen()

vim.g.godot_executable = vim.env.GODOT_EXECUTABLE

-- Auto load plugins
require("options")
require("lazy").setup("plugins")

-- Auto load commands
local commands_dir = vim.fn.stdpath("config") .. "/lua/commands"
for _, file in ipairs(vim.fn.readdir(commands_dir)) do
	if file:match("%.lua$") then
		require("commands." .. file:gsub("%.lua$", ""))
	end
end
