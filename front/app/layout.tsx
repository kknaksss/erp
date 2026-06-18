import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";

import { AuthProvider } from "@/lib/auth";
import "./globals.css";

// 본문 = Pretendard(아래 CDN link), 수치/코드 = JetBrains Mono.
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "mediness ERP",
  description: "ERP — HR workspace (연차관리)",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={`${jetbrainsMono.variable} h-full antialiased`}>
      <head>
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css"
        />
      </head>
      <body className="min-h-full bg-mgray-50 text-mgray-800">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
