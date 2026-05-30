vim.api.nvim_create_user_command("Gitdiff", function(opts)
	local cmd = "git --no-pager diff"
	if opts.args == "staged" then
		cmd = cmd .. " --staged"
	end
	vim.cmd("new")
	vim.fn.termopen(cmd)
end, {
	nargs = "?",
	complete = function()
		return { "staged" }
	end,
})

