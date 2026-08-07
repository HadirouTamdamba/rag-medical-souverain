import "./globals.css";

export const metadata = {
  title: "MediBot RAG | Sovereign Clinical Assistant",
  description: "Sovereign, self-hosted RAG assistant for hospital environments.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
