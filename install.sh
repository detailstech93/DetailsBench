#!/usr/bin/env bash
# DetailsBench: install or update on Linux with one command.
#
#   curl -fsSL https://raw.githubusercontent.com/detailstech93/DetailsBench/master/install.sh | bash
#
# or, from a copy of the project:  bash install.sh
#
# What it does (nothing else):
#   1. installs the system packages the app needs (git, Python, the Qt window libraries) with the distribution's
#      package manager: apt (Debian/Ubuntu), dnf (Fedora), pacman (Arch), zypper (openSUSE). It asks for your
#      password once (sudo).
#   2. downloads the app into ~/detailsbench (PCBM_DIR=... for another folder), or updates it there.
#   3. makes its own Python environment (.venv in that folder) and installs the Python libraries in it.
#   4. adds "DetailsBench" to the applications menu and the command detailsbench, then starts it
#      (--no-start: do not start it).
# It changes no system setting. Remove everything: delete the folder, ~/.local/bin/detailsbench and
# ~/.local/share/applications/detailsbench.desktop.
#
# Everything is in main(), so a download cut halfway runs nothing.

main() {
    set -euo pipefail
    local repo="${PCBM_REPO:-https://github.com/detailstech93/DetailsBench.git}"
    local dir="${PCBM_DIR:-$HOME/detailsbench}"
    local start=1
    for arg in "$@"; do
        case "$arg" in
            --no-start) start=0 ;;
            -h|--help) sed -n '2,20p' "$0" 2>/dev/null || true; return 0 ;;
            *) echo "Unknown option: $arg" >&2; return 2 ;;
        esac
    done

    say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
    fail() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

    [ "$(uname -s)" = "Linux" ] || fail "this installer is for Linux. On Windows see the README."
    [ "$(id -u)" -ne 0 ] || fail "run it as your own user, not as root (it asks for the password when needed)."

    # ---- 1. system packages ------------------------------------------------------------------------------------
    local id="" like=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        id="$(. /etc/os-release && echo "${ID:-}")"
        like="$(. /etc/os-release && echo "${ID_LIKE:-}")"
    fi
    local family=""
    for name in $id $like; do
        case "$name" in
            debian|ubuntu) family=apt; break ;;
            fedora|rhel|centos) family=dnf; break ;;
            arch) family=pacman; break ;;
            opensuse*|suse|sles) family=zypper; break ;;
        esac
    done
    say "System: ${id:-unknown} ($(uname -m)); package manager: ${family:-none found}"
    case "$family" in
        apt)
            sudo apt-get update
            sudo apt-get install -y git python3 python3-venv python3-pip \
                libgl1 libegl1 libfontconfig1 libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 \
                libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
                libxcb-xinerama0 libxcb-xkb1 ;;
        dnf)
            sudo dnf install -y git python3 python3-pip mesa-libGL mesa-libEGL fontconfig dbus-libs \
                libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms \
                xcb-util-renderutil ;;
        pacman)
            sudo pacman -S --needed --noconfirm git python python-pip mesa fontconfig dbus libxkbcommon \
                libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil ;;
        zypper)
            sudo zypper --non-interactive install git python3 python3-pip Mesa-libGL1 Mesa-libEGL1 fontconfig \
                libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
                libxcb-keysyms1 libxcb-render-util0 ;;
        *)
            echo "Unknown distribution: install git, Python 3.10+ (with venv and pip) and the Qt 6 xcb libraries"
            echo "yourself, then run this again. Going on with what is there."
            ;;
    esac

    command -v git >/dev/null || fail "git is missing."
    command -v python3 >/dev/null || fail "python3 is missing."
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
        || fail "Python 3.10 or newer is needed; this one is $(python3 -V 2>&1)."

    # ---- 2. the app ---------------------------------------------------------------------------------------------
    if [ -d "$dir/.git" ]; then
        say "Updating the app in $dir"
        git -C "$dir" pull --ff-only || fail "could not update $dir (changed files in it?). Nothing was removed."
    elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
        fail "$dir exists and is not this app: set PCBM_DIR to another folder."
    else
        say "Downloading the app into $dir"
        git clone --depth 1 "$repo" "$dir" || fail "could not download $repo."
    fi

    # ---- 3. Python environment -------------------------------------------------------------------------------------
    say "Installing the Python libraries (in $dir/.venv, nothing system-wide)"
    [ -x "$dir/.venv/bin/python" ] || python3 -m venv "$dir/.venv"
    "$dir/.venv/bin/python" -m pip install --upgrade pip
    "$dir/.venv/bin/python" -m pip install -r "$dir/requirements.txt"

    # ---- 4. menu entry and command -------------------------------------------------------------------------------
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
    cat > "$HOME/.local/bin/detailsbench" <<EOF
#!/usr/bin/env bash
exec "$dir/.venv/bin/python" "$dir/scripts/main.py" "\$@"
EOF
    chmod +x "$HOME/.local/bin/detailsbench"
    cat > "$HOME/.local/share/applications/detailsbench.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DetailsBench
Comment=Find, install, run and record PC benchmarks
Exec=$HOME/.local/bin/detailsbench
Terminal=false
Categories=System;Utility;
EOF

    say "Done. Start it from the applications menu or with: detailsbench"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "(~/.local/bin is not on your PATH yet: open a new terminal, or run $HOME/.local/bin/detailsbench)" ;;
    esac
    if [ "$start" -eq 1 ]; then
        if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
            say "Starting DetailsBench"
            nohup "$HOME/.local/bin/detailsbench" >/dev/null 2>&1 &
        else
            echo "No desktop session here (no DISPLAY): start it later from a desktop."
        fi
    fi
}

main "$@"
