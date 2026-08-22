import React from "react";

type Message = {
  role: "user" | "agent";
  content: string;
};

export function ChatPanel({ messages }: { messages: Message[] }) {
  return (
    <section>
      <h2>Agent Conversation</h2>
      {messages.map((message, index) => (
        <div key={index}>
          <strong>{message.role}</strong>
          <p>{message.content}</p>
        </div>
      ))}
    </section>
  );
}
