#!/usr/bin/env bash
# 🦈 Shark Calendar — Render start script
# Runs the event worker in the background, then the main app in the foreground.
# Render monitors the foreground process for health checks / restarts.

set -e

echo "🦈 Starting Shark Calendar..."
echo "🔧 Launching event worker in background..."
python shark_event_worker.py &
WORKER_PID=$!
echo "✅ Worker started (PID $WORKER_PID)"

echo "🌊 Launching main app..."
python main.py
