import ChatBox from '@/components/ChatBox';

export const metadata = {
  title: 'MediBot RAG | Sovereign Clinical Assistant',
  description: 'Sovereign, self-hosted RAG assistant for hospital environments.',
};

export default function Home() {
  return (
    <main className="app-container">
      <ChatBox />
    </main>
  );
}
