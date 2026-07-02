// Signaling client: WebSocket to the cloud signaling server.
// Handles join, message routing, heartbeat, and auto-reconnect.

export type SignalMessage = {
  type: string;
  from?: string;
  to?: string;
  [k: string]: any;
};

export type SignalingHandlers = {
  onJoined?: (msg: SignalMessage) => void;
  onPeerJoined?: (msg: SignalMessage) => void;
  onPeerLeft?: (msg: SignalMessage) => void;
  onAnswer?: (msg: SignalMessage) => void;
  onOffer?: (msg: SignalMessage) => void;
  onCandidate?: (msg: SignalMessage) => void;
  onTelemetry?: (msg: SignalMessage) => void;
  onState?: (msg: SignalMessage) => void;
  onAdvisory?: (msg: SignalMessage) => void;
  onStatusChange?: (connected: boolean) => void;
  // WebSocket video transport:
  onVideoFrame?: (msg: SignalMessage) => void; // base64 JPEG in JSON
  onBinaryFrame?: (buf: ArrayBuffer) => void; // 1 byte cam id + raw JPEG
};

export class SignalingClient {
  private ws: WebSocket | null = null;
  private hbTimer: any = null;
  private reconnectTimer: any = null;
  private closed = false;

  constructor(
    private baseUrl: string,
    private sessionId: string,
    private peerId: string,
    private role: string,
    private handlers: SignalingHandlers
  ) {}

  connect() {
    this.closed = false;
    const url = `${this.baseUrl.replace(/\/$/, "")}/ws/${this.sessionId}/${this.peerId}`;
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer"; // receive binary video frames as ArrayBuffer

    this.ws.onopen = () => {
      this.handlers.onStatusChange?.(true);
      this.send({ type: "join", role: this.role });
      this.hbTimer = setInterval(() => this.send({ type: "heartbeat" }), 10000);
    };

    this.ws.onmessage = (ev) => {
      // Binary frames (websocket video) arrive as ArrayBuffer, not text.
      if (typeof ev.data !== "string") {
        this.handlers.onBinaryFrame?.(ev.data as ArrayBuffer);
        return;
      }
      const msg: SignalMessage = JSON.parse(ev.data);
      switch (msg.type) {
        case "joined": this.handlers.onJoined?.(msg); break;
        case "peer-joined": this.handlers.onPeerJoined?.(msg); break;
        case "peer-left": this.handlers.onPeerLeft?.(msg); break;
        case "offer": this.handlers.onOffer?.(msg); break;
        case "answer": this.handlers.onAnswer?.(msg); break;
        case "candidate": this.handlers.onCandidate?.(msg); break;
        case "telemetry": this.handlers.onTelemetry?.(msg); break;
        case "state": this.handlers.onState?.(msg); break;
        case "advisory": this.handlers.onAdvisory?.(msg); break;
        case "video-frame": this.handlers.onVideoFrame?.(msg); break;
      }
    };

    this.ws.onclose = () => {
      this.handlers.onStatusChange?.(false);
      clearInterval(this.hbTimer);
      if (!this.closed) {
        // Auto-reconnect with a short backoff (session restoration).
        this.reconnectTimer = setTimeout(() => this.connect(), 2000);
      }
    };

    this.ws.onerror = () => this.ws?.close();
  }

  send(msg: SignalMessage) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  close() {
    this.closed = true;
    clearInterval(this.hbTimer);
    clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }
}
