import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SQL Doctor",
  description: "SQL 解析、计划与优化建议",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
