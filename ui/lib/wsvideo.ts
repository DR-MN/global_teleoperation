// WebSocket video viewer: renders JPEG frames pushed by the follower via the
// signaling relay into a <canvas> per camera. Used for the "websocket" transport
// modes (binary + base64) so they can be compared against WebRTC in the same UI.
//
// Camera index: 0 = global, 1 = wrist (matches the follower's cam ids).

import type { RefObject } from "react";

export class WsVideoViewer {
  // Canvas refs indexed by camera id; read .current lazily so the viewer can be
  // created before the canvases mount.
  constructor(private canvases: RefObject<HTMLCanvasElement | null>[]) {}

  // base64 JPEG in a JSON message: {cam, data}.
  onBase64(cam: number, data: string) {
    const img = new Image();
    img.onload = () => this.draw(cam, img);
    img.src = "data:image/jpeg;base64," + data;
  }

  // Binary frame: byte 0 = cam id, remaining bytes = raw JPEG.
  onBinary(buf: ArrayBuffer) {
    const bytes = new Uint8Array(buf);
    if (bytes.length < 2) return;
    const cam = bytes[0];
    const blob = new Blob([bytes.subarray(1)], { type: "image/jpeg" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      this.draw(cam, img);
      URL.revokeObjectURL(url); // free the blob once decoded
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }

  private draw(cam: number, img: HTMLImageElement) {
    const canvas = this.canvases[cam]?.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (canvas.width !== img.width) canvas.width = img.width;
    if (canvas.height !== img.height) canvas.height = img.height;
    ctx.drawImage(img, 0, 0);
  }

  close() {
    // Nothing to tear down; frames simply stop arriving when the follower leaves.
  }
}
