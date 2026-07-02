import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TeleOp Operator Console",
  description: "Global 6DOF leader-follower teleoperation operator UI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
