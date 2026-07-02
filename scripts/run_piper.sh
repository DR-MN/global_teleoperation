#!/usr/bin/env bash
# Bring up the Piper CAN bus(es) and start the teleop bridge in one command,
# so you don't run can_activate.sh by hand each time.
#
# CAN activation needs root (it runs `sudo ip link set ...`), so this will
# prompt for your sudo password once. For truly hands-free startup, set the
# interface to come up at boot instead (systemd-networkd / udev) — ask if you
# want that and skip this script.
#
# Usage:
#   scripts/run_piper.sh leader   ws_url:=wss://HOST session_id:=demo
#   scripts/run_piper.sh follower ws_url:=wss://HOST session_id:=demo
#   scripts/run_piper.sh both     ws_url:=wss://HOST session_id:=demo
#   (any extra args are passed through to `ros2 launch`)
#
# Env overrides:
#   BITRATE       CAN bitrate                       (default 1000000)
#   LEADER_CAN    leader CAN port                   (default can0)
#   FOLLOWER_CAN  follower CAN port                 (default can0; use can1 for 'both')
#   CAN_ACTIVATE  path to piper_sdk can_activate.sh (default ~/piper_sdk/piper_sdk/can_activate.sh)
set -euo pipefail

SIDE="${1:-}"
shift || true
[ -n "$SIDE" ] || { echo "usage: $0 <leader|follower|both> [ros2 launch args...]" >&2; exit 1; }

BITRATE="${BITRATE:-1000000}"
LEADER_CAN="${LEADER_CAN:-can0}"
FOLLOWER_CAN="${FOLLOWER_CAN:-can0}"
CAN_ACTIVATE="${CAN_ACTIVATE:-$HOME/piper_sdk/piper_sdk/can_activate.sh}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ -f "$CAN_ACTIVATE" ] || { echo "can_activate.sh not found at $CAN_ACTIVATE (set CAN_ACTIVATE=...)" >&2; exit 1; }

activate() {  # $1 = can port
  local port="$1"
  if ip -details link show "$port" 2>/dev/null | grep -q "state UP"; then
    echo ">> $port already up — skipping activation"
  else
    echo ">> activating $port @ ${BITRATE} (sudo)"
    bash "$CAN_ACTIVATE" "$port" "$BITRATE"
  fi
}

case "$SIDE" in
  leader)   activate "$LEADER_CAN" ;;
  follower) activate "$FOLLOWER_CAN" ;;
  both)
    [ "$LEADER_CAN" != "$FOLLOWER_CAN" ] || {
      echo "For 'both', leader and follower need different buses (e.g. LEADER_CAN=can0 FOLLOWER_CAN=can1)." >&2
      echo "Two USB-CAN adapters usually need can_muti_activate.sh with USB addresses; activate them yourself, then re-run." >&2; exit 1; }
    activate "$LEADER_CAN"; activate "$FOLLOWER_CAN" ;;
  *) echo "unknown side: $SIDE (leader|follower|both)" >&2; exit 1 ;;
esac

# Source ROS + this workspace so `ros2 launch` is found.
[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
[ -f "$REPO_ROOT/install/setup.bash" ] && source "$REPO_ROOT/install/setup.bash"

# Build the Piper launch args for the chosen side.
case "$SIDE" in
  leader)   ARGS="side:=leader   leader_arm:=piper   leader_can_port:=$LEADER_CAN" ;;
  follower) ARGS="side:=follower follower_arm:=piper follower_can_port:=$FOLLOWER_CAN" ;;
  both)     ARGS="side:=both leader_arm:=piper leader_can_port:=$LEADER_CAN follower_arm:=piper follower_can_port:=$FOLLOWER_CAN" ;;
esac

echo ">> ros2 launch teleop_bridge teleop_bridge.launch.py $ARGS $*"
exec ros2 launch teleop_bridge teleop_bridge.launch.py $ARGS "$@"
