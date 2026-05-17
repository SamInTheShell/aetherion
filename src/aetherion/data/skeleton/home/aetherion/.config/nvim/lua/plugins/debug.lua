return {
	{
		"mfussenegger/nvim-dap",
		dependencies = {
			"rcarriga/nvim-dap-ui",
			"nvim-neotest/nvim-nio",
			"mason-org/mason.nvim",
			"jay-babu/mason-nvim-dap.nvim",
		},
		config = function()
			local dap = require("dap")
			local dapui = require("dapui")

			-- Setup dap-ui
			dapui.setup()

			-- Auto open/close dap-ui
			dap.listeners.after.event_initialized["dapui_config"] = function()
				dapui.open()
			end
			dap.listeners.before.event_terminated["dapui_config"] = function()
				dapui.close()
			end
			dap.listeners.before.event_exited["dapui_config"] = function()
				dapui.close()
			end

			-- DAP signs
			vim.fn.sign_define("DapBreakpoint", { text = "🔴", texthl = "", linehl = "", numhl = "" })
			vim.fn.sign_define("DapBreakpointCondition", { text = "🟡", texthl = "", linehl = "", numhl = "" })
			vim.fn.sign_define("DapLogPoint", { text = "📝", texthl = "", linehl = "", numhl = "" })
			vim.fn.sign_define("DapStopped", { text = "➡️", texthl = "", linehl = "", numhl = "" })
			vim.fn.sign_define("DapBreakpointRejected", { text = "❌", texthl = "", linehl = "", numhl = "" })

			-- Key mappings
			vim.keymap.set("n", "<F5>", dap.continue, { desc = "Debug: Start/Continue" })
			vim.keymap.set("n", "<F6>", function()
				dap.terminate()
				dapui.close()
			end)
			vim.keymap.set("n", "<F1>", dap.step_into, { desc = "Debug: Step Into" })
			vim.keymap.set("n", "<F2>", dap.step_over, { desc = "Debug: Step Over" })
			vim.keymap.set("n", "<F3>", dap.step_out, { desc = "Debug: Step Out" })
			vim.keymap.set("n", "<leader>b", dap.toggle_breakpoint, { desc = "Debug: Toggle Breakpoint" })
			vim.keymap.set("n", "<leader>B", function()
				dap.set_breakpoint(vim.fn.input("Breakpoint condition: "))
			end, { desc = "Debug: Set Conditional Breakpoint" })
			vim.keymap.set("n", "<F7>", dapui.toggle, { desc = "Debug: See last session result" })

			-- Godot DAP adapter
			dap.adapters.godot = {
				type = "server",
				host = "127.0.0.1",
				port = 6006,
			}

			-- Delve (Go) adapter — uses the system `dlv` baked into the image
			-- via `go install` at build time; mason-nvim-dap is not involved.
			dap.adapters.delve = function(callback, config)
				if config.mode == "remote" and config.request == "attach" then
					callback({
						type = "server",
						host = config.host or "127.0.0.1",
						port = config.port or "38697",
					})
				else
					callback({
						type = "server",
						port = "${port}",
						executable = {
							command = "dlv",
							args = { "dap", "-l", "127.0.0.1:${port}" },
							detached = vim.fn.has("win32") == 0,
						},
					})
				end
			end
			dap.configurations.go = {
				{ type = "delve", name = "Debug", request = "launch", program = "${file}" },
				{ type = "delve", name = "Debug test", request = "launch", mode = "test", program = "${file}" },
				{ type = "delve", name = "Debug test (go.mod)", request = "launch", mode = "test", program = "./${relativeFileDirname}" },
			}

			-- Godot DAP configurations
			dap.configurations.gdscript = {
				{
					type = "godot",
					request = "launch",
					name = "Launch Godot Project",
					project = "${workspaceFolder}",
					launch_game_instance = true,
					launch_scene = false,
				},
				{
					type = "godot",
					request = "launch",
					name = "Launch Current Scene",
					project = "${workspaceFolder}",
					launch_game_instance = true,
					launch_scene = true,
				},
				{
					type = "godot",
					request = "attach",
					name = "Attach to Running Godot",
					project = "${workspaceFolder}",
				},
			}

			-- Python (debugpy) — pip-installed system-wide in Dockerfile
			-- stage 3d. Invoked as `python3 -m debugpy.adapter`.
			dap.adapters.python = {
				type = "executable",
				command = "python3",
				args = { "-m", "debugpy.adapter" },
			}
			dap.configurations.python = {
				{
					type = "python",
					request = "launch",
					name = "Launch file",
					program = "${file}",
					pythonPath = function()
						local cwd = vim.fn.getcwd()
						for _, candidate in ipairs({ ".venv/bin/python", "venv/bin/python", ".env/bin/python" }) do
							local p = cwd .. "/" .. candidate
							if vim.fn.executable(p) == 1 then
								return p
							end
						end
						return "python3"
					end,
				},
			}

			-- Rust / C / C++ (codelldb) — extracted from the upstream vsix
			-- into /opt/codelldb by Dockerfile stage 3d.
			dap.adapters.codelldb = {
				type = "server",
				port = "${port}",
				executable = {
					command = "codelldb",
					args = { "--port", "${port}" },
				},
			}

			-- JavaScript / TypeScript (vscode-js-debug). We register the two
			-- node-side adapter names that the launch configs use.
			for _, name in ipairs({ "pwa-node", "node" }) do
				dap.adapters[name] = {
					type = "server",
					host = "localhost",
					port = "${port}",
					executable = {
						command = "node",
						args = {
							"/opt/js-debug-adapter/js-debug/src/dapDebugServer.js",
							"${port}",
						},
					},
				}
			end
			for _, lang in ipairs({ "javascript", "typescript", "javascriptreact", "typescriptreact" }) do
				dap.configurations[lang] = {
					{
						type = "pwa-node",
						request = "launch",
						name = "Launch file",
						program = "${file}",
						cwd = "${workspaceFolder}",
					},
				}
			end

			-- Mason is kept loaded for ad-hoc `:Mason` use only — nothing is
			-- auto-installed at startup because every adapter above already
			-- resolves to a system path baked into the image.
			require("mason-nvim-dap").setup({
				automatic_installation = false,
				ensure_installed = {},
				handlers = {},
			})
		end,
	},
}
