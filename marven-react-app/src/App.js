import React from "react";
import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import Home from "./pages/Home";
import RichChat from "./pages/RichChat";

function App() {
  return (
    <BrowserRouter>
      <div style={{ padding: 12, borderBottom: "1px solid #eee", marginBottom: 12 }}>
        <nav style={{ display: "flex", gap: 12 }}>
          <Link to="/">Home (non-stream)</Link>
          <Link to="/rich-chat">Rich Chat (streaming)</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/rich-chat" element={<RichChat />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
