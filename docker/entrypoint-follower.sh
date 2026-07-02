#!/usr/bin/env bash
# Sources ROS2 + the bridge workspace, then launches the requested side and the
# WebRTC video publisher. Endpoint/session come from env (no hardcoded infra).
set -euo pipefail

source /opt/ros/humble/setup.bash
source /opt/teleop/ros2_ws/install/setup.bash

SIDE="${1:-follower}"
ZENOH_ENDPOINT="${ZENOH_ENDPOINT:-}"
SESSION_ID="${SESSION_ID:-default}"
SIGNALING_URL="${SIGNALING_URL:-ws://signaling:8080}"

# Start the WebRTC video publisher in the background (follower only).
if [[ "$SIDE" == "follower" ]]; then
  python3 -m teleop.video.run_publisher \
    --signaling "$SIGNALING_URL" --session "$SESSION_ID" &
fi

exec ros2 run "teleop_bridge" "${SIDE}_bridge" \
  --ros-args -p zenoh_endpoint:="$ZENOH_ENDPOINT" -p session_id:="$SESSION_ID"
