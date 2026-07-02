"use client";

import { useEffect, useRef, useState } from "react";
import { SignalingClient } from "../lib/signaling";
import { VideoViewer } from "../lib/webrtc";
import { WsVideoViewer } from "../lib/wsvideo";

const SIGNALING_URL =
  process.env.NEXT_PUBLIC_SIGNALING_URL || "ws://localhost:8080";

// Session falls back to an env var then "default" for the server-rendered pass;
// the URL (?session=demo) is applied on the client in an effect to keep
// hydration deterministic. This lets the operator watch any named session
// without a rebuild — e.g. https://…/?session=demo to match a follower on
// --session demo.
const DEFAULT_SESSION = process.env.NEXT_PUBLIC_SESSION || "default";

type RobotState = {
  positions?: number[];
  status?: string;
  gripper_position?: number;
};
type Telemetry = {
  command_latency_ms?: number;
  packet_loss?: number;
  connected?: boolean;
};
type Advisory = { severity: string; category: string; message: string };

export default function Console() {
  const [connected, setConnected] = useState(false);
  const [session, setSession] = useState(DEFAULT_SESSION);
  const [state, setState] = useState<RobotState>({});
  const [telemetry, setTelemetry] = useState<Telemetry>({});
  const [advisories, setAdvisories] = useState<Advisory[]>([]);
  const [peerConn, setPeerConn] = useState<string>("new");
  // Which transport the follower advertised: "webrtc" (default) or "websocket".
  const [videoMode, setVideoMode] = useState<"webrtc" | "websocket">("webrtc");

  const globalRef = useRef<HTMLVideoElement>(null);
  const wristRef = useRef<HTMLVideoElement>(null);
  const globalCanvasRef = useRef<HTMLCanvasElement>(null);
  const wristCanvasRef = useRef<HTMLCanvasElement>(null);
  const signalingRef = useRef<SignalingClient | null>(null);
  const viewerRef = useRef<VideoViewer | null>(null);
  const wsViewerRef = useRef<WsVideoViewer | null>(null);

  // Apply ?session= from the URL on the client (after hydration).
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("session");
    if (q && q !== session) setSession(q);
  }, []);

  useEffect(() => {
    const peerId = "viewer-" + Math.random().toString(36).slice(2, 8);

    const attach = (idx: number, stream: MediaStream) => {
      const el = idx === 0 ? globalRef.current : wristRef.current;
      if (el) el.srcObject = stream;
    };

    // WebSocket-video renderer (canvases). Frames only arrive when the follower
    // uses the websocket transport, so creating it unconditionally is harmless.
    wsViewerRef.current = new WsVideoViewer([globalCanvasRef, wristCanvasRef]);

    // Connect to a follower using whichever transport it advertised. WebRTC needs
    // us to send an offer; the websocket transport just pushes frames to render.
    const connectToFollower = (follower: any) => {
      const transport = follower?.video?.transport === "websocket" ? "websocket" : "webrtc";
      setVideoMode(transport);
      if (transport === "webrtc") viewerRef.current?.connectTo(follower.peer_id);
    };

    const sig = new SignalingClient(SIGNALING_URL, session, peerId, "viewer", {
      onStatusChange: setConnected,
      onJoined: (msg) => {
        const viewer = new VideoViewer(
          sig,
          msg.iceServers || [{ urls: "stun:stun.l.google.com:19302" }],
          attach,
          (st) => setPeerConn(st)
        );
        viewerRef.current = viewer;
        const follower = (msg.peers || []).find((p: any) => p.role === "follower");
        if (follower) connectToFollower(follower);
      },
      onPeerJoined: (msg) => {
        if (msg.role === "follower") connectToFollower(msg);
      },
      onAnswer: (msg) => viewerRef.current?.onAnswer(msg.sdp),
      onCandidate: (msg) => viewerRef.current?.onRemoteCandidate(msg.candidate),
      onVideoFrame: (msg) => wsViewerRef.current?.onBase64(msg.cam, msg.data),
      onBinaryFrame: (buf) => wsViewerRef.current?.onBinary(buf),
      onState: (msg) => setState(msg.state || msg),
      onTelemetry: (msg) => setTelemetry(msg.telemetry || msg),
      onAdvisory: (msg) => setAdvisories(msg.advisories || []),
    });
    signalingRef.current = sig;
    sig.connect();

    return () => {
      viewerRef.current?.close();
      wsViewerRef.current?.close();
      sig.close();
    };
  }, [session]);

  const latency = telemetry.command_latency_ms ?? 0;
  const latencyClass =
    latency > 500 ? "CRITICAL" : latency > 300 ? "WARNING" : "INFO";

  return (
    <div className="app">
      <div className="header">
        <h1>TeleOp Operator Console</h1>
        <span>
          <span className={"dot " + (connected ? "ok" : "bad")} />
          signaling {connected ? "connected" : "disconnected"}
        </span>
        <span>session: {session}</span>
        <span>video: {videoMode === "webrtc" ? peerConn : "websocket"}</span>
        <span>transport: {videoMode}</span>
      </div>

      <div className="videos">
        <div className="tile">
          <span className="label">Global camera</span>
          <video
            ref={globalRef}
            autoPlay
            playsInline
            muted
            style={{ display: videoMode === "webrtc" ? "block" : "none" }}
          />
          <canvas
            ref={globalCanvasRef}
            style={{ display: videoMode === "websocket" ? "block" : "none" }}
          />
        </div>
        <div className="tile">
          <span className="label">Wrist camera</span>
          <video
            ref={wristRef}
            autoPlay
            playsInline
            muted
            style={{ display: videoMode === "webrtc" ? "block" : "none" }}
          />
          <canvas
            ref={wristCanvasRef}
            style={{ display: videoMode === "websocket" ? "block" : "none" }}
          />
        </div>
      </div>

      <div className="side">
        <div className="panel">
          <h2>Network</h2>
          <div className="kv">
            <span>Command latency</span>
            <span className={"adv " + latencyClass} style={{ padding: "0 6px" }}>
              {latency.toFixed(0)} ms
            </span>
          </div>
          <div className="kv">
            <span>Packet loss</span>
            <span>{((telemetry.packet_loss ?? 0) * 100).toFixed(1)} %</span>
          </div>
          <div className="kv">
            <span>Link</span>
            <span>{telemetry.connected === false ? "LOST" : "up"}</span>
          </div>
        </div>

        <div className="panel">
          <h2>Robot State</h2>
          <div className="kv">
            <span>Status</span>
            <span>{state.status ?? "—"}</span>
          </div>
          <div className="kv">
            <span>Gripper</span>
            <span>{state.gripper_position?.toFixed(2) ?? "—"}</span>
          </div>
          {(state.positions || []).map((q, i) => (
            <div className="kv" key={i}>
              <span>q{i}</span>
              <span>{q.toFixed(3)} rad</span>
            </div>
          ))}
        </div>

        <div className="panel">
          <h2>Supervisor Advisories</h2>
          {advisories.length === 0 && (
            <div className="adv INFO">Nominal. Continue teleoperation.</div>
          )}
          {advisories.map((a, i) => (
            <div className={"adv " + a.severity} key={i}>
              [{a.severity}] {a.category}: {a.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
