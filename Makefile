.PHONY: help build install publish clean

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; print "Available targets:\n"} \
	      /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' \
	      $(MAKEFILE_LIST)

build: clean ## Build sdist + wheel into dist/
	uv build

install: build ## Build then `uv tool install` the wheel, replacing any existing aetherion install
	uv tool install --force --reinstall dist/aetherion-*.whl

publish: build ## Upload dist/* to PyPI (requires UV_PUBLISH_TOKEN or ~/.pypirc)
	uv publish

clean: ## Remove build artifacts (dist/, build/, *.egg-info, __pycache__)
	rm -rf dist/ build/ src/*.egg-info
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
