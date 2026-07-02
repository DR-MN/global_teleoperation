// WebRTC viewer: requests video from the follower publisher.
// The viewer creates the offer (recvonly) and the publisher answers with its
// global + wrist camera tracks; tracks arrive in negotiation order.

import { SignalingClient } from "./signaling";

export class VideoViewer {
  private pc: RTCPeerConnection | null = null;
  private tracks: MediaStreamTrack[] = [];

  constructor(
    private signaling: SignalingClient,
    private iceServers: RTCIceServer[],
    private onTrack: (index: number, stream: MediaStream) => void,
    private onConnectionState?: (state: RTCPeerConnectionState) => void
  ) {}

  // Begin negotiation with a specific follower publisher peer.
  async connectTo(publisherPeerId: string) {
    const pc = new RTCPeerConnection({ iceServers: this.iceServers });
    this.pc = pc;

    // Two recvonly transceivers: [0] global, [1] wrist.
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("video", { direction: "recvonly" });

    pc.ontrack = (ev) => {
      const idx = this.tracks.length;
      this.tracks.push(ev.track);
      this.onTrack(idx, new MediaStream([ev.track]));
    };

    pc.onicecandidate = (ev) => {
      if (ev.candidate) {
        this.signaling.send({
          type: "candidate",
          to: publisherPeerId,
          candidate: {
            candidate: ev.candidate.candidate,
            sdpMid: ev.candidate.sdpMid,
            sdpMLineIndex: ev.candidate.sdpMLineIndex,
          },
        });
      }
    };

    pc.onconnectionstatechange = () => this.onConnectionState?.(pc.connectionState);

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    this.signaling.send({ type: "offer", to: publisherPeerId, sdp: offer.sdp });
  }

  async onAnswer(sdp: string) {
    await this.pc?.setRemoteDescription({ type: "answer", sdp });
  }

  async onRemoteCandidate(candidate: any) {
    try {
      await this.pc?.addIceCandidate(candidate);
    } catch {
      /* ignore late/duplicate candidates */
    }
  }

  close() {
    this.pc?.close();
    this.pc = null;
    this.tracks = [];
  }
}
