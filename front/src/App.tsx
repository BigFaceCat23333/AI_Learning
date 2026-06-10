import { useState } from "react";
import UploadPanel from "./components/UploadPanel";
import ChatPanel from "./components/ChatPanel";

export default function App() {
  const [hasUploadedDocs, setHasUploadedDocs] = useState(false);

  return (
    <div className="app">
      <header className="app-header">
        <h1>AI Learning</h1>
        <span className="app-subtitle">文档解读工作台</span>
      </header>
      <main className="app-main">
        <aside className="app-sidebar">
          <UploadPanel onUploadSuccess={() => setHasUploadedDocs(true)} />
        </aside>
        <section className="app-content">
          <ChatPanel hasDocuments={hasUploadedDocs} />
        </section>
      </main>
    </div>
  );
}
