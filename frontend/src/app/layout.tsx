import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "X漱石",
  description: "思想蒸留型RAGによるAI対話体験",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-stone-50 text-stone-900">
        {children}
      </body>
    </html>
  );
}
