#!/usr/bin/env bash
# Computer（Grok 版）一键安装 / 更新脚本
#
#   curl -fsSL https://raw.githubusercontent.com/Kairitsu/computer/main/install.sh | bash
#
# 已经装过的话，再运行一次同一条命令就是更新。可以在命令末尾加参数，例如：
#
#   curl -fsSL https://raw.githubusercontent.com/Kairitsu/computer/main/install.sh | bash -s -- --port 8080
#
#   --dir DIR       代码放在哪里（默认 ~/.local/share/computer，环境变量 CPTR_INSTALL_DIR）
#   --host HOST     监听地址（默认 0.0.0.0，环境变量 CPTR_HOST）
#   --port PORT     端口（默认 8000，环境变量 CPTR_PORT）
#   --branch NAME   跟踪的分支（默认 main，环境变量 CPTR_BRANCH）
#   --no-service    不创建后台服务，装完自己启动
#   --no-grok       不安装 Grok CLI
#   --allow-root    允许用 root 安装（不推荐）
#
# 端口、监听地址和要不要后台服务会记在 <安装目录>/.tools/install.conf 里，
# 以后再运行这条命令更新时沿用，不用重新输入参数。
# 数据目录沿用 cptr 的默认值 ~/.cptr；想换位置，安装前设置 CPTR_DATA_DIR。

set -euo pipefail

REPO_URL="${CPTR_REPO_URL:-https://github.com/Kairitsu/computer.git}"
INSTALL_DIR="${CPTR_INSTALL_DIR:-$HOME/.local/share/computer}"
# 留空表示没指定：先看上次安装记下的值，再用默认值（见 load_settings）。
HOST="${CPTR_HOST:-}"
PORT="${CPTR_PORT:-}"
WITH_SERVICE=""
BRANCH="${CPTR_BRANCH:-main}"
SERVICE_NAME="${CPTR_SERVICE_NAME:-computer}"
NODE_MAJOR=22
WITH_GROK=1
ALLOW_ROOT=0

if [ -t 1 ]; then
	BOLD=$'\033[1m' DIM=$'\033[2m' RED=$'\033[31m' GREEN=$'\033[32m' YELLOW=$'\033[33m' RESET=$'\033[0m'
else
	BOLD='' DIM='' RED='' GREEN='' YELLOW='' RESET=''
fi

step() { printf '\n%s==>%s %s%s%s\n' "$GREEN" "$RESET" "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s警告：%s%s\n' "$YELLOW" "$*" "$RESET" >&2; }
die() {
	printf '%s错误：%s%s\n' "$RED" "$*" "$RESET" >&2
	exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
	cat <<'EOF'
用法：curl -fsSL https://raw.githubusercontent.com/Kairitsu/computer/main/install.sh | bash -s -- [参数]

  --dir DIR       代码放在哪里（默认 ~/.local/share/computer）
  --host HOST     监听地址（默认 0.0.0.0）
  --port PORT     端口（默认 8000）
  --branch NAME   跟踪的分支（默认 main）
  --no-service    不创建后台服务，装完自己启动（--service 改回来）
  --no-grok       不安装 Grok CLI
  --allow-root    允许用 root 安装（不推荐）

已经装过的话，再运行一次就是更新，上次用的端口等设置会沿用。
EOF
}

parse_args() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--dir) INSTALL_DIR="${2:?--dir 需要一个路径}"; shift ;;
		--host) HOST="${2:?--host 需要一个地址}"; shift ;;
		--port) PORT="${2:?--port 需要一个端口号}"; shift ;;
		--branch) BRANCH="${2:?--branch 需要一个分支名}"; shift ;;
		--service) WITH_SERVICE=1 ;;
		--no-service) WITH_SERVICE=0 ;;
		--no-grok) WITH_GROK=0 ;;
		--allow-root) ALLOW_ROOT=1 ;;
		-h | --help)
			usage
			exit 0
			;;
		*) die "不认识的参数：$1" ;;
		esac
		shift
	done
}

settings_file() { printf '%s/.tools/install.conf' "$INSTALL_DIR"; }

load_settings() {
	local key value
	if [ -f "$(settings_file)" ]; then
		while IFS='=' read -r key value; do
			case "$key" in
			HOST) [ -n "$HOST" ] || HOST="$value" ;;
			PORT) [ -n "$PORT" ] || PORT="$value" ;;
			WITH_SERVICE) [ -n "$WITH_SERVICE" ] || WITH_SERVICE="$value" ;;
			esac
		done <"$(settings_file)"
	fi
	HOST="${HOST:-0.0.0.0}"
	PORT="${PORT:-8000}"
	WITH_SERVICE="${WITH_SERVICE:-1}"
	case "$PORT" in '' | *[!0-9]*) die "端口必须是数字：$PORT" ;; esac
}

save_settings() {
	mkdir -p "$INSTALL_DIR/.tools"
	printf 'HOST=%s\nPORT=%s\nWITH_SERVICE=%s\n' "$HOST" "$PORT" "$WITH_SERVICE" >"$(settings_file)"
}

check_platform() {
	OS="$(uname -s)"
	ARCH="$(uname -m)"
	case "$OS" in
	Linux | Darwin) ;;
	*) die "目前只支持 Linux 和 macOS（Windows 请在 WSL 里运行）。" ;;
	esac
	if [ "$(id -u)" -eq 0 ] && [ "$ALLOW_ROOT" -ne 1 ]; then
		die "请不要用 root 安装：登录网页的人能做到运行服务的用户能做的一切。
    建议新建一个普通用户再运行这条命令；确实要用 root，就在命令末尾加上 --allow-root。"
	fi
	have git || die "没有找到 git。请先安装它（Debian/Ubuntu：sudo apt install git；macOS：xcode-select --install）。"
	have curl || die "没有找到 curl。"
	have tar || die "没有找到 tar。"
}

# ── 工具链 ───────────────────────────────────────────────────

ensure_uv() {
	UV="$(command -v uv 2>/dev/null || true)"
	for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
		[ -n "$UV" ] && break
		[ -x "$candidate" ] && UV="$candidate"
	done
	if [ -z "$UV" ]; then
		step "安装 uv（用来管理 Python 和依赖）"
		curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
		UV="$HOME/.local/bin/uv"
		[ -x "$UV" ] || die "uv 安装失败。"
	fi
	info "uv：$("$UV" --version)"
}

node_ok() {
	# Vite 需要 Node.js ^20.19 或 >= 22.12。
	local version major minor
	version="$("$1" -v 2>/dev/null)" || return 1
	version="${version#v}"
	major="${version%%.*}"
	minor="${version#*.}"
	minor="${minor%%.*}"
	case "$major$minor" in '' | *[!0-9]*) return 1 ;; esac
	[ "$major" -gt 22 ] || { [ "$major" -eq 22 ] && [ "$minor" -ge 12 ]; } || { [ "$major" -eq 20 ] && [ "$minor" -ge 19 ]; }
}

ensure_node() {
	local local_node="$INSTALL_DIR/.tools/node/bin"
	if [ -x "$local_node/node" ] && node_ok "$local_node/node"; then
		NODE_BIN="$local_node"
	elif have node && have npm && node_ok "$(command -v node)"; then
		NODE_BIN="$(dirname "$(command -v node)")"
	else
		step "下载 Node.js $NODE_MAJOR（只给这个项目用，不影响系统）"
		local platform arch base file sums
		case "$OS" in Linux) platform=linux ;; Darwin) platform=darwin ;; esac
		case "$ARCH" in
		x86_64 | amd64) arch=x64 ;;
		aarch64 | arm64) arch=arm64 ;;
		*) die "没有适合 $ARCH 的 Node.js 预编译包，请自己安装 Node.js 22 后重试。" ;;
		esac
		base="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
		sums="$(curl -fsSL "$base/SHASUMS256.txt")" || die "无法从 nodejs.org 下载 Node.js。"
		file="$(printf '%s\n' "$sums" | awk -v want="-$platform-$arch.tar.gz" 'index($2, want) && $2 ~ /^node-v/ {print $2; exit}')"
		[ -n "$file" ] || die "找不到 Node.js $NODE_MAJOR 的 $platform-$arch 安装包。"
		local tmp
		tmp="$(mktemp -d)"
		curl -fsSL "$base/$file" -o "$tmp/$file"
		local expected actual
		expected="$(printf '%s\n' "$sums" | awk -v f="$file" '$2 == f {print $1}')"
		if have sha256sum; then
			actual="$(sha256sum "$tmp/$file" | awk '{print $1}')"
		else
			actual="$(shasum -a 256 "$tmp/$file" | awk '{print $1}')"
		fi
		[ "$expected" = "$actual" ] || die "Node.js 安装包校验失败。"
		rm -rf "$INSTALL_DIR/.tools/node"
		mkdir -p "$INSTALL_DIR/.tools/node"
		tar -xzf "$tmp/$file" -C "$INSTALL_DIR/.tools/node" --strip-components=1
		rm -rf "$tmp"
		NODE_BIN="$local_node"
	fi
	export PATH="$NODE_BIN:$PATH"
	info "Node.js：$(node -v)（$NODE_BIN）"
}

ensure_grok() {
	[ "$WITH_GROK" -eq 1 ] || return 0
	if have grok || [ -x "$HOME/.local/bin/grok" ]; then
		info "Grok CLI：已安装"
		return 0
	fi
	step "安装 Grok CLI"
	if ! curl -fsSL https://x.ai/cli/install.sh | bash; then
		warn "Grok CLI 安装失败。可以稍后手动安装：curl -fsSL https://x.ai/cli/install.sh | bash"
	fi
}

# ── 代码 ─────────────────────────────────────────────────────

fetch_code() {
	OLD_HEAD=""
	if [ -d "$INSTALL_DIR/.git" ]; then
		step "更新代码（$INSTALL_DIR）"
		OLD_HEAD="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
		if [ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]; then
			git -C "$INSTALL_DIR" status --short --untracked-files=no >&2
			die "$INSTALL_DIR 里有未提交的修改，为了不覆盖它们，先停在这里。"
		fi
		git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
		if [ "$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
			git -C "$INSTALL_DIR" checkout --quiet "$BRANCH" 2>/dev/null ||
				git -C "$INSTALL_DIR" checkout --quiet -b "$BRANCH" --track "origin/$BRANCH"
		fi
		git -C "$INSTALL_DIR" merge --quiet --ff-only "origin/$BRANCH" ||
			die "本地代码和 GitHub 上的版本已经分叉，无法自动更新。"
	else
		if [ -d "$INSTALL_DIR" ]; then
			local entry
			for entry in "$INSTALL_DIR"/* "$INSTALL_DIR"/.[!.]*; do
				if [ -e "$entry" ] && [ "$entry" != "$INSTALL_DIR/.tools" ]; then
					die "$INSTALL_DIR 已经存在而且不是空目录。换一个位置（--dir）或者先清理它。"
				fi
			done
		fi
		step "下载代码到 $INSTALL_DIR"
		mkdir -p "$(dirname "$INSTALL_DIR")"
		if [ -d "$INSTALL_DIR/.tools" ]; then
			# 先下载的 Node.js 放在这里了；把仓库克隆到旁边再挪进来。
			local tmp="$INSTALL_DIR.clone.$$"
			git clone --quiet --branch "$BRANCH" "$REPO_URL" "$tmp"
			mv "$INSTALL_DIR/.tools" "$tmp/.tools"
			rmdir "$INSTALL_DIR"
			mv "$tmp" "$INSTALL_DIR"
		else
			git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
		fi
	fi
	NEW_HEAD="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
	info "版本：$(git -C "$INSTALL_DIR" log -1 --format='%h %s')"
}

build() {
	local frontend="$INSTALL_DIR/cptr/frontend"
	step "构建前端"
	(
		cd "$frontend"
		local log
		log="$(mktemp)"
		if ! npm ci --no-audit --no-fund >"$log" 2>&1; then
			tail -n 40 "$log" >&2
			die "前端依赖安装失败。"
		fi
		rm -rf build.next
		# 先构建到 build.next，成功了再换上去，正在运行的服务不会读到半成品。
		if ! CPTR_FRONTEND_OUT=build.next npm run build >"$log" 2>&1 || [ ! -f build.next/index.html ]; then
			tail -n 40 "$log" >&2
			die "前端构建失败。"
		fi
		rm -f "$log"
		rm -rf build.old
		[ ! -d build ] || mv build build.old
		mv build.next build
		rm -rf build.old
	)
	step "安装后端依赖"
	(cd "$INSTALL_DIR" && "$UV" sync --frozen --extra all --quiet)
	[ -x "$INSTALL_DIR/.venv/bin/cptr" ] || die "后端安装失败。"
}

# ── 后台服务 ─────────────────────────────────────────────────

service_path() {
	local dirs
	dirs="$NODE_BIN:$(dirname "$UV"):$HOME/.local/bin"
	if have grok; then dirs="$dirs:$(dirname "$(command -v grok)")"; fi
	dirs="$dirs:/usr/local/bin:/usr/bin:/bin"
	[ "$OS" = Darwin ] && dirs="$dirs:/opt/homebrew/bin"
	# 去重，保持顺序
	printf '%s' "$dirs" | tr ':' '\n' | awk 'NF && !seen[$0]++' | paste -sd: -
}

data_dir_env_line() {
	[ -n "${CPTR_DATA_DIR:-}" ] && printf 'Environment=CPTR_DATA_DIR=%s\n' "$CPTR_DATA_DIR"
	return 0
}

write_systemd_unit() {
	# $1: system | user
	local user_line="" wanted="multi-user.target"
	if [ "$1" = system ]; then
		user_line="User=$(id -un)"
	else
		wanted="default.target"
	fi
	cat <<EOF
[Unit]
Description=Computer (Grok)
After=network-online.target
Wants=network-online.target

[Service]
${user_line}
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$(service_path)
$(data_dir_env_line)
ExecStart=$INSTALL_DIR/.venv/bin/cptr run --host $HOST --port $PORT --headless
Restart=on-failure
RestartSec=3

[Install]
WantedBy=$wanted
EOF
}

can_sudo() {
	have sudo || return 1
	sudo -n true 2>/dev/null && return 0
	# 通过管道运行时，sudo 会直接在终端上问密码；没有终端就只能装成用户服务。
	(exec </dev/tty) 2>/dev/null || return 1
	info "创建系统服务需要 sudo，接下来可能要输入密码。不想用 sudo 的话，按 Ctrl+C 后加上 --no-service 重新运行。"
	sudo -v
}

setup_service() {
	SERVICE_KIND=none
	[ "$WITH_SERVICE" -eq 1 ] || return 0
	if [ "$OS" = Linux ] && have systemctl && [ -d /run/systemd/system ]; then
		step "配置后台服务（systemd：$SERVICE_NAME）"
		if can_sudo; then
			write_systemd_unit system | sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null
			sudo systemctl daemon-reload
			sudo systemctl enable --quiet "$SERVICE_NAME"
			sudo systemctl restart "$SERVICE_NAME"
			SERVICE_KIND=system
		else
			local dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
			mkdir -p "$dir"
			write_systemd_unit user >"$dir/$SERVICE_NAME.service"
			systemctl --user daemon-reload
			systemctl --user enable --quiet "$SERVICE_NAME"
			systemctl --user restart "$SERVICE_NAME"
			if ! loginctl enable-linger "$(id -un)" 2>/dev/null; then
				warn "没能开启 linger，退出登录后服务可能会停止。可以让管理员运行：sudo loginctl enable-linger $(id -un)"
			fi
			SERVICE_KIND=user
		fi
	elif [ "$OS" = Darwin ]; then
		step "配置后台服务（launchd）"
		local label="site.computer.$SERVICE_NAME"
		local plist="$HOME/Library/LaunchAgents/$label.plist"
		local logs="${CPTR_DATA_DIR:-$HOME/.cptr}/logs"
		mkdir -p "$HOME/Library/LaunchAgents" "$logs"
		cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$label</string>
	<key>ProgramArguments</key>
	<array>
		<string>$INSTALL_DIR/.venv/bin/cptr</string>
		<string>run</string>
		<string>--host</string><string>$HOST</string>
		<string>--port</string><string>$PORT</string>
		<string>--headless</string>
	</array>
	<key>WorkingDirectory</key><string>$INSTALL_DIR</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key><string>$(service_path)</string>
		$([ -n "${CPTR_DATA_DIR:-}" ] && printf '<key>CPTR_DATA_DIR</key><string>%s</string>' "$CPTR_DATA_DIR")
	</dict>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>StandardOutPath</key><string>$logs/service.log</string>
	<key>StandardErrorPath</key><string>$logs/service.log</string>
</dict>
</plist>
EOF
		launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
		launchctl bootstrap "gui/$(id -u)" "$plist"
		SERVICE_KIND=launchd
	else
		warn "这台机器上没有 systemd，跳过后台服务。"
	fi
}

# ── 收尾 ─────────────────────────────────────────────────────

local_url() {
	case "$HOST" in 0.0.0.0 | 127.0.0.1 | localhost | ::) printf 'http://127.0.0.1:%s' "$PORT" ;; *) printf 'http://%s:%s' "$HOST" "$PORT" ;; esac
}

lan_address() {
	local ip=""
	if [ "$OS" = Linux ]; then
		ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
	else
		ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
	fi
	printf '%s' "${ip:-localhost}"
}

finish() {
	local version
	version="$("$INSTALL_DIR/.venv/bin/python" -c 'from importlib.metadata import version; print(version("cptr"))' 2>/dev/null || echo '?')"
	local run_cmd="$INSTALL_DIR/.venv/bin/cptr run --host $HOST --port $PORT --headless"

	if [ "$SERVICE_KIND" = none ]; then
		step "安装完成（v$version）"
		info "用下面这条命令启动，启动后终端里会打印首次设置的地址："
		info "  $run_cmd"
		return 0
	fi

	local base
	base="$(local_url)"
	local up=0
	for _ in $(seq 1 60); do
		if curl -fsS "$base/api/health" >/dev/null 2>&1; then
			up=1
			break
		fi
		sleep 1
	done
	if [ "$up" -ne 1 ]; then
		warn "服务没有按时启动。"
		case "$SERVICE_KIND" in
		system) info "看看日志：sudo journalctl -u $SERVICE_NAME -n 50" ;;
		user) info "看看日志：journalctl --user -u $SERVICE_NAME -n 50" ;;
		launchd) info "看看日志：${CPTR_DATA_DIR:-$HOME/.cptr}/logs/service.log" ;;
		esac
		exit 1
	fi

	local public_host="$HOST"
	case "$HOST" in 0.0.0.0 | ::) public_host="$(lan_address)" ;; esac
	local url="http://$public_host:$PORT"

	if [ -n "$OLD_HEAD" ]; then
		if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
			step "已经是最新版本（v$version），服务已重启"
		else
			step "已更新到 v$version，服务已重启"
		fi
		info "打开：$url"
		return 0
	fi

	step "安装完成（v$version）"
	if curl -fsS "$base/api/config" 2>/dev/null | grep -q '"needs_setup":true'; then
		local token
		token="$(cat "${CPTR_DATA_DIR:-$HOME/.cptr}/startup-token" 2>/dev/null || true)"
		info "第一次访问请打开下面的地址，创建管理员账号："
		info "  ${BOLD}$url/?token=$token${RESET}"
		info "${DIM}这个链接只在首次设置时有用，每次重启服务都会换新的。${RESET}"
	else
		info "打开：$url"
	fi
	info ""
	info "以后在网页左下角菜单里点「检查更新」就能升级；也可以再运行一次这条安装命令。"
	if [ "$WITH_GROK" -eq 1 ]; then
		info "Grok CLI 还没登录的话，到网页里的「设置 → 用量」登录即可。"
	fi
}

main() {
	parse_args "$@"
	load_settings
	check_platform
	printf '%sComputer（Grok 版）安装程序%s\n' "$BOLD" "$RESET"
	info "安装位置：$INSTALL_DIR"
	mkdir -p "$(dirname "$INSTALL_DIR")"
	ensure_uv
	ensure_node
	ensure_grok
	fetch_code
	build
	save_settings
	setup_service
	finish
}

main "$@"
