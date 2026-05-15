import "../styles/globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "UAT Agents",
  description: "AI-controlled browser workspace for UAT workflows",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
