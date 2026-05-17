vim.cmd("set expandtab")
vim.cmd("set number")
vim.opt.numberwidth = 4
vim.opt.signcolumn = "yes:2"
vim.opt.splitright = true
vim.opt.splitbelow = true
vim.opt.wrap = false

-- Enable mouse support for native-like selection
vim.opt.mouse = "a"
vim.opt.mousefocus = true

-- OSC 52 clipboard provider for SSH sessions
local function osc52_copy(text)
	local base64_text = vim.base64.encode(text)
	local osc52_sequence = string.format("\027]52;c;%s\007", base64_text)
	io.write(osc52_sequence)
	io.flush()
end

-- Detect if we're in an SSH session
local function is_ssh_session()
	return vim.env.SSH_CLIENT ~= nil or vim.env.SSH_TTY ~= nil or vim.env.SSH_CONNECTION ~= nil
end

-- System clipboard integration
if is_ssh_session() then
	-- Use OSC 52 for SSH sessions
	vim.g.clipboard = {
		name = "OSC 52",
		copy = {
			["+"] = function(lines, regtype)
				osc52_copy(table.concat(lines, "\n"))
			end,
			["*"] = function(lines, regtype)
				osc52_copy(table.concat(lines, "\n"))
			end,
		},
		paste = {
			["+"] = function()
				return vim.split(vim.fn.getreg("+"), "\n")
			end,
			["*"] = function()
				return vim.split(vim.fn.getreg("*"), "\n")
			end,
		},
	}
else
	-- Don't automatically sync with system clipboard for local sessions
	-- We'll handle clipboard writing manually for y and x operations
end

-- Command-line completion
vim.opt.wildmenu = true
vim.opt.wildmode = "longest:full,full"
vim.opt.wildoptions = "pum"
vim.opt.pumheight = 10

-- New panels with directional window creation
-- <leader>n + arrow keys / hjkl for directional window creation
vim.keymap.set("n", "<leader>n<Up>", ":above new<CR>", { desc = "New window above current" })
vim.keymap.set("n", "<leader>nk", ":above new<CR>", { desc = "New window above current" })
vim.keymap.set("n", "<leader>n<Down>", ":below new<CR>", { desc = "New window below current" })
vim.keymap.set("n", "<leader>nj", ":below new<CR>", { desc = "New window below current" })
vim.keymap.set("n", "<leader>n<Left>", ":leftabove vnew<CR>", { desc = "New window left of current" })
vim.keymap.set("n", "<leader>nh", ":leftabove vnew<CR>", { desc = "New window left of current" })
vim.keymap.set("n", "<leader>n<Right>", ":rightbelow vnew<CR>", { desc = "New window right of current" })
vim.keymap.set("n", "<leader>nl", ":rightbelow vnew<CR>", { desc = "New window right of current" })
vim.keymap.set("n", "<leader>nn", ":new<CR>", { desc = "New window below current" })
vim.keymap.set("n", "<leader>q", ":bd<CR>")
vim.keymap.set("n", "<leader>Q", ":bd!<CR>")
vim.keymap.set("n", "<leader>rh", ":horizontal resize ")
vim.keymap.set("n", "<leader>rv", ":vertical resize ")
vim.keymap.set("n", "<leader>rs", ":vertical resize 60<CR>")

-- Make y copy to system clipboard in addition to default behavior
vim.keymap.set({ "n", "v" }, "y", function()
	-- First do the normal yank to nvim's default register
	vim.cmd('normal! "' .. vim.v.register .. "y")
	-- Then also copy to system clipboard (write-only)
	local text = vim.fn.getreg(vim.v.register)
	if is_ssh_session() then
		osc52_copy(text)
	else
		vim.fn.setreg("+", text)
	end
end, { desc = "Yank to nvim register and system clipboard" })

-- Paste (only from nvim's internal registers, not system clipboard)
-- Normal paste operations use nvim's default register
-- No special mappings needed - p and P work with nvim's internal clipboard

-- Cut (x goes to both vim registers and system clipboard)
vim.keymap.set("v", "x", function()
	-- First do normal cut to vim register
	vim.cmd('normal! d')
	-- Then also copy to system clipboard
	local text = vim.fn.getreg('"')
	if is_ssh_session() then
		osc52_copy(text)
	else
		vim.fn.setreg("+", text)
	end
end, { desc = "Cut to vim register and system clipboard" })

vim.keymap.set("n", "x", function()
	-- First do normal cut to vim register
	vim.cmd('normal! dl')
	-- Then also copy to system clipboard
	local text = vim.fn.getreg('"')
	if is_ssh_session() then
		osc52_copy(text)
	else
		vim.fn.setreg("+", text)
	end
end, { desc = "Cut character to vim register and system clipboard" })

vim.keymap.set("v", "<D-x>", '"+d', { desc = "Cut to system clipboard" })
vim.keymap.set("v", "<C-x>", '"+d', { desc = "Cut to system clipboard" })

-- Delete operations work normally in vim but don't write to system clipboard
-- d, dd, D will save to vim registers for pasting with p, but won't go to system clipboard

-- Select all
vim.keymap.set("n", "<D-a>", "ggVG", { desc = "Select all" })

-- Better visual mode behavior
vim.keymap.set("v", "<", "<gv", { desc = "Indent and reselect" })
vim.keymap.set("v", ">", ">gv", { desc = "Unindent and reselect" })

-- Show line extensions and whitespace
vim.opt.list = true
vim.opt.listchars = {
	extends = "→",
	precedes = "←",
	tab = "┊ ", -- Shows tabs as a subtle vertical line
	trail = "·", -- Shows trailing spaces
	space = "·", -- Shows all spaces (toggle with :set list!)
	nbsp = "⦸", -- Shows non-breaking spaces
}

-- Toggle whitespace visibility
vim.keymap.set("n", "<leader>tw", ":set list!<CR>", { desc = "Toggle whitespace visibility" })

-- Toggle mouse support
vim.keymap.set({ "n", "v" }, "<leader>tm", function()
	if vim.o.mouse == "a" then
		vim.o.mouse = ""
		print("Mouse disabled")
	else
		vim.o.mouse = "a"
		print("Mouse enabled")
	end
end, { desc = "Toggle mouse support" })

-- Auto-detect indentation type (tabs vs spaces)
local function detect_indentation()
	local buf = vim.api.nvim_get_current_buf()
	local lines = vim.api.nvim_buf_get_lines(buf, 0, 100, false) -- Check first 100 lines

	local tab_count = 0
	local space_count = 0
	local space_indent_sizes = {}

	for _, line in ipairs(lines) do
		if line:match("^%s+") then -- Line starts with whitespace
			if line:match("^\t") then
				tab_count = tab_count + 1
			elseif line:match("^ ") then
				space_count = space_count + 1
				-- Count leading spaces to detect indent size
				local spaces = line:match("^( +)")
				if spaces then
					local count = #spaces
					space_indent_sizes[count] = (space_indent_sizes[count] or 0) + 1
				end
			end
		end
	end

	-- Decide based on what we found
	if tab_count > space_count then
		-- Use tabs
		vim.bo.expandtab = false
		vim.bo.tabstop = 4
		vim.bo.shiftwidth = 4
		vim.bo.softtabstop = 4
	elseif space_count > 0 then
		-- Use spaces, detect most common indent size
		local most_common_size = 2 -- default
		local max_count = 0
		for size, count in pairs(space_indent_sizes) do
			if count > max_count and (size == 2 or size == 4 or size == 8) then
				most_common_size = size
				max_count = count
			end
		end

		vim.bo.expandtab = true
		vim.bo.tabstop = most_common_size
		vim.bo.shiftwidth = most_common_size
		vim.bo.softtabstop = most_common_size
	end
end

-- Auto-detect indentation when opening files
vim.api.nvim_create_autocmd({ "BufRead", "BufNewFile" }, {
	callback = function()
		-- Skip for very small files or empty files
		local line_count = vim.api.nvim_buf_line_count(0)
		if line_count > 5 then
			detect_indentation()
		end
	end,
})

-- Manual command to re-detect indentation
vim.api.nvim_create_user_command("DetectIndent", detect_indentation, { desc = "Detect and set indentation type" })

-- Make 'w' stop at end of line instead of wrapping to next line
local function my_w_motion()
	local initial_line = vim.fn.line(".")
	local initial_col = vim.fn.col(".")
	local line_length = vim.fn.col("$") - 1 -- col('$') gives position after last char

	-- If we're already at the end of the line, allow normal 'w' to go to next line
	if initial_col >= line_length then
		vim.cmd("normal! w")
		return
	end

	vim.cmd("normal! w")
	local new_line = vim.fn.line(".")

	-- If we jumped to a new line (and we weren't already at the end), go back to end of previous line
	if initial_line ~= new_line then
		vim.cmd("normal! k$")
	end
end

vim.keymap.set("n", "w", my_w_motion, { desc = "Word forward (stops at end of line)" })

-- Fix Go comment block auto-indent
vim.api.nvim_create_autocmd("FileType", {
	pattern = "go",
	callback = function()
		-- Add 'r' and 'o' to formatoptions for Go files:
		-- r: Insert comment leader when pressing Enter in Insert mode
		-- o: Insert comment leader when using 'o' or 'O' in Normal mode
		-- This enables auto-indent continuation in /* */ comment blocks
		vim.opt_local.formatoptions:append("ro")
	end,
})

-- Enhanced H and L behavior: scroll half page if already at top/bottom
local function smart_H()
	local current_line = vim.fn.line(".")
	local top_line = vim.fn.line("w0")

	if current_line == top_line then
		-- Smooth scroll half page up
		local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
		if cinnamon_ok then
			cinnamon.scroll(vim.keycode("<C-u>"))
		else
			vim.cmd("normal! " .. vim.keycode("<C-u>"))
		end
	else
		vim.cmd("normal! H")
	end
end

local function smart_L()
	local current_line = vim.fn.line(".")
	local bottom_line = vim.fn.line("w$")

	if current_line == bottom_line then
		-- Smooth scroll half page down
		local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
		if cinnamon_ok then
			cinnamon.scroll(vim.keycode("<C-d>"))
		else
			vim.cmd("normal! " .. vim.keycode("<C-d>"))
		end
	else
		vim.cmd("normal! L")
	end
end

vim.keymap.set("n", "H", smart_H, { desc = "Move to top line or scroll half page up" })
vim.keymap.set("n", "L", smart_L, { desc = "Move to bottom line or scroll half page down" })
vim.keymap.set("v", "H", smart_H, { desc = "Move to top line or scroll half page up" })
vim.keymap.set("v", "L", smart_L, { desc = "Move to bottom line or scroll half page down" })

-- Smooth scrolling for Ctrl+d and Ctrl+u
vim.keymap.set({ "n", "v" }, "<C-d>", function()
	local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
	if cinnamon_ok then
		cinnamon.scroll("<C-d>")
	else
		vim.cmd("normal! " .. vim.keycode("<C-d>"))
	end
end, { desc = "Scroll half page down (smooth)" })

vim.keymap.set({ "n", "v" }, "<C-u>", function()
	local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
	if cinnamon_ok then
		cinnamon.scroll("<C-u>")
	else
		vim.cmd("normal! " .. vim.keycode("<C-u>"))
	end
end, { desc = "Scroll half page up (smooth)" })

-- Smooth scrolling for Page Up and Page Down
vim.keymap.set({ "n", "v" }, "<PageUp>", function()
	local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
	if cinnamon_ok then
		cinnamon.scroll("<C-b>")
	else
		vim.cmd("normal! " .. vim.keycode("<C-b>"))
	end
end, { desc = "Scroll page up (smooth)" })

vim.keymap.set({ "n", "v" }, "<PageDown>", function()
	local cinnamon_ok, cinnamon = pcall(require, "cinnamon")
	if cinnamon_ok then
		cinnamon.scroll("<C-f>")
	else
		vim.cmd("normal! " .. vim.keycode("<C-f>"))
	end
end, { desc = "Scroll page down (smooth)" })

-- Clear screen on exit
vim.api.nvim_create_autocmd("VimLeave", {
	callback = function()
		vim.cmd("!clear")
	end,
})
