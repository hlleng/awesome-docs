# Minimal makefile for Sphinx documentation
#

# You can set these variables from the command line, and also
# from the environment for the first two.
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build
# 翻译目标语言（英文）。中文为源语言，无需翻译文件。
LANGS         ?= en
DOCS_ASSISTANT_PORT ?= 8300
DOCS_ASSISTANT_PYTHON ?= python3

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile clean html gettext intl serve livehtml

clean:
	@$(SPHINXBUILD) -M clean "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

html:
	@echo "sphinx build (zh_CN)..."
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# 提取所有可翻译字符串到 build/gettext/*.pot
gettext:
	@$(SPHINXBUILD) -M gettext "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

# 用 .pot 更新各语言 .po 文件（locale/<lang>/LC_MESSAGES/*.po）
intl: gettext
	@sphinx-intl update -p "$(BUILDDIR)/gettext" -l $(LANGS)

# 构建英文 HTML（需先翻译 locale/en 下的 .po）
html-en:
	@echo "sphinx build (en)..."
	@$(SPHINXBUILD) -b html -D language=en "$(SOURCEDIR)" "$(BUILDDIR)/html-en" $(SPHINXOPTS) $(O)

# 本地预览：构建后启动静态服务器
# 端口可覆盖：make serve PORT=9000
PORT ?= 8200
# 允许通过本机局域网 IP 打开预览。可用 DOCS_ASSISTANT_PUBLIC_HOST 覆盖页面中
# 的 API 主机名，例如：make serve DOCS_ASSISTANT_PUBLIC_HOST=10.0.0.10。
DOCS_ASSISTANT_HOST ?= 0.0.0.0
DOCS_ASSISTANT_PUBLIC_HOST ?= $(shell hostname -I 2>/dev/null | awk '{print $$1}')
ifeq ($(strip $(DOCS_ASSISTANT_PUBLIC_HOST)),)
DOCS_ASSISTANT_PUBLIC_HOST = 127.0.0.1
endif
# 本地预览只允许回环地址和页面使用的 LAN 地址；多网卡时可通过
# DOCS_ASSISTANT_PUBLIC_HOST 同时覆盖页面 API 地址和允许的来源。
DOCS_ASSISTANT_ALLOWED_ORIGINS ?= http://127.0.0.1:$(PORT),http://localhost:$(PORT),http://0.0.0.0:$(PORT),http://$(DOCS_ASSISTANT_PUBLIC_HOST):$(PORT)
DOCS_ASSISTANT_DB_PATH ?= $(CURDIR)/.docs-assistant/docs-assistant.sqlite3
serve: export DOCS_ASSISTANT_API_URL = http://$(DOCS_ASSISTANT_PUBLIC_HOST):$(DOCS_ASSISTANT_PORT)/api/docs-assistant
serve: html
	@echo "Starting docs preview at http://0.0.0.0:$(PORT) (Ctrl+C to stop)"
	@set -eu; \
	assistant_pid=; \
	cleanup() { \
		status=$$?; \
		if [ -n "$$assistant_pid" ] && kill -0 "$$assistant_pid" 2>/dev/null; then \
			kill "$$assistant_pid" 2>/dev/null || true; \
			wait "$$assistant_pid" 2>/dev/null || true; \
		fi; \
		exit "$$status"; \
	}; \
	trap cleanup INT TERM EXIT; \
	if ! $(DOCS_ASSISTANT_PYTHON) -c 'import fastapi, uvicorn' >/dev/null 2>&1; then \
		echo "FastAPI/Uvicorn are missing; run: $(DOCS_ASSISTANT_PYTHON) -m pip install -r tools/docs_assistant/requirements.txt" >&2; \
		exit 1; \
	fi; \
	echo "Starting docs assistant at http://$(DOCS_ASSISTANT_PUBLIC_HOST):$(DOCS_ASSISTANT_PORT)/api/docs-assistant (listening on $(DOCS_ASSISTANT_HOST))"; \
	DOCS_ASSISTANT_HOST="$(DOCS_ASSISTANT_HOST)" DOCS_ASSISTANT_PORT="$(DOCS_ASSISTANT_PORT)" DOCS_ASSISTANT_DB_PATH="$(DOCS_ASSISTANT_DB_PATH)" DOCS_ASSISTANT_ALLOWED_ORIGINS="$(DOCS_ASSISTANT_ALLOWED_ORIGINS)" $(DOCS_ASSISTANT_PYTHON) -m uvicorn tools.docs_assistant.server:app --host "$(DOCS_ASSISTANT_HOST)" --port "$(DOCS_ASSISTANT_PORT)" --workers 1 & \
	assistant_pid=$$!; \
	ready=0; \
	for _ in $$(seq 1 50); do \
		if curl -fsS "http://127.0.0.1:$(DOCS_ASSISTANT_PORT)/health" >/dev/null 2>&1; then ready=1; break; fi; \
		if ! kill -0 "$$assistant_pid" 2>/dev/null; then \
			echo "docs assistant exited before becoming healthy" >&2; \
			wait "$$assistant_pid" || true; \
			exit 1; \
		fi; \
		sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then \
		echo "docs assistant health check timed out" >&2; \
		exit 1; \
	fi; \
	echo "Serving docs at http://0.0.0.0:$(PORT)"; \
	python3 -m http.server $(PORT) --directory "$(BUILDDIR)/html"
