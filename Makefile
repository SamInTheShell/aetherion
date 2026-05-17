.PHONY: help build publish

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; print "Available targets:\n"} \
	      /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' \
	      $(MAKEFILE_LIST)

build: ## Build sdist + wheel into dist/
	rm -rf dist/
	uv build

publish: build ## Upload dist/* to PyPI (requires UV_PUBLISH_TOKEN or ~/.pypirc)
	uv publish
