return {
	{
		"mason-org/mason.nvim",
		config = function()
			require("mason").setup({
				pip = {
					upgrade_pip = false,
					-- install_args = { "--no-cache-dir" },
				},
				-- log_level = vim.log.levels.DEBUG,
			})
		end,
	},
	{
		"mason-org/mason-lspconfig.nvim",
		config = function()
			-- All LSP binaries are baked into the image system-wide (see
			-- Dockerfile stage 3d and the bun/go installs in stage 6). Mason
			-- is kept around only for ad-hoc `:Mason` use later — we don't
			-- let it manage anything automatically because its install path
			-- is too racy in headless contexts.
			require("mason-lspconfig").setup({
				ensure_installed = {},
				automatic_installation = false,
			})
		end,
	},
	{
		"neovim/nvim-lspconfig",
		config = function()
			-- Configure diagnostics with modern API
			vim.diagnostic.config({
				signs = {
					text = {
						[vim.diagnostic.severity.ERROR] = "E",
						[vim.diagnostic.severity.WARN] = "W",
						[vim.diagnostic.severity.INFO] = "I",
						[vim.diagnostic.severity.HINT] = "H",
					},
					priority = 5, -- Lower priority than gitsigns (10)
				},
			})

			-- LSP keybindings
			vim.api.nvim_create_autocmd("LspAttach", {
				group = vim.api.nvim_create_augroup("UserLspConfig", {}),
				callback = function(ev)
					local opts = { buffer = ev.buf }
					vim.keymap.set("n", "gD", vim.lsp.buf.declaration, vim.tbl_extend("force", opts, { desc = "Go to declaration" }))
					vim.keymap.set("n", "gd", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "Go to definition" }))
					vim.keymap.set("n", "K", vim.lsp.buf.hover, vim.tbl_extend("force", opts, { desc = "Hover documentation" }))
					vim.keymap.set("n", "gi", vim.lsp.buf.implementation, vim.tbl_extend("force", opts, { desc = "Go to implementation" }))
					vim.keymap.set("n", "<C-k>", vim.lsp.buf.signature_help, vim.tbl_extend("force", opts, { desc = "Signature help" }))
					vim.keymap.set("n", "<space>wa", vim.lsp.buf.add_workspace_folder, vim.tbl_extend("force", opts, { desc = "Add workspace folder" }))
					vim.keymap.set("n", "<space>wr", vim.lsp.buf.remove_workspace_folder, vim.tbl_extend("force", opts, { desc = "Remove workspace folder" }))
					vim.keymap.set("n", "<space>wl", function()
						print(vim.inspect(vim.lsp.buf.list_workspace_folders()))
					end, vim.tbl_extend("force", opts, { desc = "List workspace folders" }))
					vim.keymap.set("n", "<space>D", vim.lsp.buf.type_definition, vim.tbl_extend("force", opts, { desc = "Type definition" }))
					vim.keymap.set("n", "<space>rn", vim.lsp.buf.rename, vim.tbl_extend("force", opts, { desc = "Rename symbol" }))
					vim.keymap.set({ "n", "v" }, "<space>ca", vim.lsp.buf.code_action, vim.tbl_extend("force", opts, { desc = "Code action" }))
					vim.keymap.set("n", "gr", vim.lsp.buf.references, vim.tbl_extend("force", opts, { desc = "Go to references" }))
					vim.keymap.set("n", "<space>f", function()
						vim.lsp.buf.format({ async = true })
					end, vim.tbl_extend("force", opts, { desc = "Format buffer" }))
					vim.keymap.set("n", "<space>e", function()
						vim.diagnostic.open_float({ focusable = false })
					end, vim.tbl_extend("force", opts, { desc = "Show line diagnostics" }))
					vim.keymap.set("n", "[d", vim.diagnostic.goto_prev, vim.tbl_extend("force", opts, { desc = "Previous diagnostic" }))
					vim.keymap.set("n", "]d", vim.diagnostic.goto_next, vim.tbl_extend("force", opts, { desc = "Next diagnostic" }))
					vim.keymap.set("n", "<space>q", vim.diagnostic.setloclist, vim.tbl_extend("force", opts, { desc = "Diagnostic loclist" }))
				end,
			})

			-- Godot LSP Configuration
			vim.lsp.config.gdscript = {
				cmd = { "nc", "localhost", "6005" },
				filetypes = { "gd", "gdscript", "gdscript3" },
				root_dir = function(fname)
					return vim.fs.dirname(vim.fs.find({ "project.godot", ".git" }, { path = fname, upward = true })[1])
				end,
			}

			-- Go LSP configuration
			vim.lsp.config.gopls = {
				cmd = { "gopls" },
				filetypes = { "go", "gomod", "gowork", "gotmpl" },
				settings = {
					gopls = {
						analyses = {
							unusedparams = true,
						},
						staticcheck = true,
						gofumpt = true,
					},
				},
			}

			-- Python LSP configuration
			vim.lsp.config.pyright = {
				cmd = { "pyright-langserver", "--stdio" },
				filetypes = { "python" },
				root_dir = function(fname)
					if type(fname) ~= "string" then
						return nil
					end
					local found = vim.fs.find(
						{ "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".git" },
						{ path = fname, upward = true }
					)
					return found[1] and vim.fs.dirname(found[1]) or nil
				end,
				settings = {
					python = {
						analysis = {
							typeCheckingMode = "basic",
							autoSearchPaths = true,
							useLibraryCodeForTypes = true,
							autoImportCompletions = true,
						},
						pythonPath = function()
							-- Check for virtual environment in current working directory
							local cwd = vim.fn.getcwd()
							local venv_paths = {
								cwd .. "/.venv/bin/python",
								cwd .. "/venv/bin/python",
								cwd .. "/.env/bin/python",
							}

							for _, path in ipairs(venv_paths) do
								if vim.fn.executable(path) == 1 then
									return path
								end
							end

							-- Fallback to system python
							return vim.fn.exepath("python3") or vim.fn.exepath("python")
						end,
					},
				},
				on_new_config = function(new_config, new_root_dir)
					-- Set python path based on root directory
					local python_path = nil
					local venv_paths = {
						new_root_dir .. "/.venv/bin/python",
						new_root_dir .. "/venv/bin/python",
						new_root_dir .. "/.env/bin/python",
					}

					for _, path in ipairs(venv_paths) do
						if vim.fn.executable(path) == 1 then
							python_path = path
							break
						end
					end

					if python_path then
						new_config.settings.python.pythonPath = python_path
					end
				end,
			}

			-- TypeScript/JavaScript LSP configuration
			vim.lsp.config.ts_ls = {
				cmd = { "typescript-language-server", "--stdio" },
				filetypes = {
					"javascript",
					"javascriptreact",
					"javascript.jsx",
					"typescript",
					"typescriptreact",
					"typescript.tsx",
				},
				settings = {
					typescript = {
						inlayHints = {
							includeInlayParameterNameHints = "all",
							includeInlayParameterNameHintsWhenArgumentMatchesName = false,
							includeInlayFunctionParameterTypeHints = true,
							includeInlayVariableTypeHints = true,
							includeInlayPropertyDeclarationTypeHints = true,
							includeInlayFunctionLikeReturnTypeHints = true,
							includeInlayEnumMemberValueHints = true,
						},
					},
					javascript = {
						inlayHints = {
							includeInlayParameterNameHints = "all",
							includeInlayParameterNameHintsWhenArgumentMatchesName = false,
							includeInlayFunctionParameterTypeHints = true,
							includeInlayVariableTypeHints = true,
							includeInlayPropertyDeclarationTypeHints = true,
							includeInlayFunctionLikeReturnTypeHints = true,
							includeInlayEnumMemberValueHints = true,
						},
					},
				},
			}

			-- Rust LSP configuration
			vim.lsp.config.rust_analyzer = {
				cmd = { "rust-analyzer" },
				filetypes = { "rust" },
				settings = {
					["rust-analyzer"] = {
						cargo = {
							allFeatures = true,
							loadOutDirsFromCheck = true,
							runBuildScripts = true,
						},
						checkOnSave = true,
						check = {
							allFeatures = true,
							command = "clippy",
						},
						procMacro = {
							enable = true,
							ignored = {
								["async-trait"] = { "async_trait" },
								["napi-derive"] = { "napi" },
								["async-recursion"] = { "async_recursion" },
							},
						},
					},
				},
			}

			-- Lua LSP — system lua-language-server (Dockerfile stage 3d).
			vim.lsp.config.lua_ls = {
				cmd = { "lua-language-server" },
				filetypes = { "lua" },
				settings = {
					Lua = {
						runtime = { version = "LuaJIT" },
						diagnostics = { globals = { "vim" } },
						workspace = {
							library = vim.api.nvim_get_runtime_file("", true),
							checkThirdParty = false,
						},
						telemetry = { enable = false },
					},
				},
			}

			-- Vim-script LSP — bun-installed vim-language-server in $PATH.
			vim.lsp.config.vimls = {
				cmd = { "vim-language-server", "--stdio" },
				filetypes = { "vim" },
			}

			-- Auto-start each configured server on its matching filetypes.
			-- mason-lspconfig used to do this for us; with ensure_installed
			-- empty we wire it ourselves.
			vim.lsp.enable({
				"lua_ls",
				"vimls",
				"gopls",
				"pyright",
				"ts_ls",
				"rust_analyzer",
				"gdscript",
			})
		end,
	},
}
